from collections import OrderedDict
from logging import getLogger
from typing import Iterable, List, Optional, Set

from django.db import transaction

from requests import RequestException

from gnosis.eth import EthereumClient

from ..models import InternalTx, InternalTxDecoded, SafeMasterCopy
from .ethereum_indexer import EthereumIndexer
from .tx_decoder import CannotDecode, get_safe_tx_decoder

logger = getLogger(__name__)


class InternalTxIndexerProvider:
    def __new__(cls):
        if not hasattr(cls, 'instance'):
            from django.conf import settings
            if settings.ETH_INTERNAL_NO_FILTER:
                cls.instance = InternalTxIndexerWithTraceBlock(EthereumClient(settings.ETHEREUM_TRACING_NODE_URL),
                                                               block_process_limit=settings.ETH_INTERNAL_TXS_BLOCK_PROCESS_LIMIT)
            else:
                cls.instance = InternalTxIndexer(EthereumClient(settings.ETHEREUM_TRACING_NODE_URL),
                                                 block_process_limit=settings.ETH_INTERNAL_TXS_BLOCK_PROCESS_LIMIT)
        return cls.instance

    @classmethod
    def del_singleton(cls):
        if hasattr(cls, "instance"):
            del cls.instance


class InternalTxIndexer(EthereumIndexer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tx_decoder = get_safe_tx_decoder()
        self.number_trace_blocks = 10  # Use `trace_block` for last `number_trace_blocks` blocks indexing

    @property
    def database_field(self):
        return 'tx_block_number'

    @property
    def database_model(self):
        return SafeMasterCopy

    def find_relevant_elements(self, addresses: List[str], from_block_number: int,
                               to_block_number: int, current_block_number: Optional[int] = None) -> Set[str]:
        current_block_number = current_block_number or self.ethereum_client.current_block_number
        # Use `trace_block` for last `number_trace_blocks` blocks and `trace_filter` for the others
        trace_block_number = max(current_block_number - self.number_trace_blocks, 0)
        if from_block_number > trace_block_number:  # Just trace_block
            logger.info('Using trace_block from-block=%d to-block=%d', from_block_number, to_block_number)
            return self._find_relevant_elements_using_trace_block(addresses, from_block_number, to_block_number)
        elif to_block_number < trace_block_number:  # Just trace_filter
            logger.info('Using trace_filter from-block=%d to-block=%d', from_block_number, to_block_number)
            return self._find_relevant_elements_using_trace_filter(addresses, from_block_number, to_block_number)
        else:  # trace_filter for old blocks and trace_filter for the most recent ones
            logger.info('Using trace_filter from-block=%d to-block=%d and trace_block from-block=%d to-block=%d',
                        from_block_number, trace_block_number, trace_block_number, to_block_number)
            return OrderedDict.fromkeys(list(self._find_relevant_elements_using_trace_filter(addresses,
                                                                                             from_block_number,
                                                                                             trace_block_number)) +
                                        list(self._find_relevant_elements_using_trace_block(addresses,
                                                                                            trace_block_number,
                                                                                            to_block_number))
                                        ).keys()

    def _find_relevant_elements_using_trace_block(self, addresses: List[str], from_block_number: int,
                                                  to_block_number: int) -> Set[str]:
        try:
            block_numbers = list(range(from_block_number, to_block_number + 1))
            traces = self.ethereum_client.parity.trace_blocks(block_numbers)
            tx_hashes = []
            for block_number, trace_list in zip(block_numbers, traces):
                if not trace_list:
                    logger.warning('Empty `trace_block` for block=%d', block_number)
                tx_hashes.extend([trace['transactionHash'] for trace in trace_list
                                  if trace.get('action', {}).get('from') in addresses
                                  or trace.get('action', {}).get('to') in addresses])
            return OrderedDict.fromkeys(tx_hashes).keys()
        except RequestException as e:
            raise self.FindRelevantElementsException('Request error calling `trace_block`') from e

    def _find_relevant_elements_using_trace_filter(self, addresses: List[str], from_block_number: int,
                                                   to_block_number: int) -> Set[str]:
        """
        Search for tx hashes with internal txs (in and out) of a `address`
        :param addresses:
        :param from_block_number: Starting block number
        :param to_block_number: Ending block number
        :return: Tx hashes of txs with internal txs relevant for the `addresses`
        """
        logger.debug('Searching for internal txs from block-number=%d to block-number=%d - Addresses=%s',
                     from_block_number, to_block_number, addresses)

        try:
            to_traces = self.ethereum_client.parity.trace_filter(from_block=from_block_number,
                                                                 to_block=to_block_number,
                                                                 to_address=addresses)

            from_traces = self.ethereum_client.parity.trace_filter(from_block=from_block_number,
                                                                   to_block=to_block_number,
                                                                   from_address=addresses)
        except RequestException as e:
            raise self.FindRelevantElementsException('Request error calling `trace_filter`') from e

        # Log INFO if traces found, DEBUG if not
        tx_hashes = OrderedDict.fromkeys([trace['transactionHash']
                                          for trace in (to_traces + from_traces)]).keys()
        log_fn = logger.info if len(tx_hashes) else logger.debug
        log_fn('Found %d relevant txs with %d internal txs between block-number=%d and block-number=%d. Addresses=%s',
               len(to_traces + from_traces), len(tx_hashes), from_block_number, to_block_number, addresses)

        return tx_hashes

    def process_elements(self, tx_hashes: Iterable[str]) -> List[InternalTx]:
        # Prefetch ethereum txs
        if not tx_hashes:
            return []

        logger.info('Prefetching and storing ethereum txs')
        ethereum_txs = self.index_service.txs_create_or_update_from_tx_hashes(tx_hashes)
        logger.info('End prefetching and storing of ethereum txs')

        logger.info('Prefetching of traces(internal txs)')
        internal_txs_batch = [InternalTx.objects.build_from_trace(trace, ethereum_tx)
                              for ethereum_tx, traces
                              in zip(ethereum_txs, self.ethereum_client.parity.trace_transactions(tx_hashes))
                              for trace in traces]
        logger.info('End prefetching of traces(internal txs)')

        logger.info('Storing traces')

        with transaction.atomic():
            internal_txs = InternalTx.objects.bulk_create(internal_txs_batch, ignore_conflicts=True)
            logger.info('End storing of traces')
            logger.info('Decoding of traces')
            internal_txs_decoded_batch = []
            for internal_tx in internal_txs:
                if internal_tx.can_be_decoded:
                    if internal_tx.pk is None:  # Internal tx not created, already exists
                        internal_tx = InternalTx.objects.get(ethereum_tx=internal_tx.ethereum_tx,
                                                             trace_address=internal_tx.trace_address)
                    try:
                        function_name, arguments = self.tx_decoder.decode_transaction(bytes(internal_tx.data))
                        internal_txs_decoded_batch.append(InternalTxDecoded(internal_tx=internal_tx,
                                                                            function_name=function_name,
                                                                            arguments=arguments,
                                                                            processed=False))
                    except CannotDecode:
                        pass
            if internal_txs_decoded_batch:
                InternalTxDecoded.objects.bulk_create(internal_txs_decoded_batch, ignore_conflicts=True)
            logger.info('End decoding of traces')
            return internal_txs


class InternalTxIndexerWithTraceBlock(InternalTxIndexer):
    """
    Optimize receiving all the addresses
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.updated_blocks_behind: int = 2641600  # Hack to process all the addresses together

    def find_relevant_elements(self, addresses: List[str], from_block_number: int,
                               to_block_number: int, current_block_number: Optional[int] = None) -> Set[str]:
        current_block_number = current_block_number or self.ethereum_client.current_block_number
        # Use `trace_block` for last `number_trace_blocks` blocks and `trace_filter` for the others
        trace_block_number = max(current_block_number - self.number_trace_blocks, 0)
        logger.info('Using trace_block from-block=%d to-block=%d', from_block_number, to_block_number)
        return self._find_relevant_elements_using_trace_block(addresses, from_block_number, to_block_number)
