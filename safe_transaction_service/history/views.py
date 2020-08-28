import hashlib

from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page

import django_filters
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.filters import OrderingFilter
from rest_framework.generics import (DestroyAPIView, ListAPIView,
                                     ListCreateAPIView, RetrieveAPIView,
                                     get_object_or_404)
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView
from web3 import Web3

from safe_transaction_service.version import __version__

from .filters import (DefaultPagination, MultisigTransactionFilter,
                      SmallPagination, TransferListFilter)
from .models import (InternalTx, ModuleTransaction, MultisigTransaction,
                     SafeContract, SafeContractDelegate, SafeStatus)
from .serializers import (OwnerResponseSerializer,
                          SafeBalanceResponseSerializer,
                          SafeBalanceUsdResponseSerializer,
                          SafeCreationInfoResponseSerializer,
                          SafeDelegateDeleteSerializer,
                          SafeDelegateResponseSerializer,
                          SafeDelegateSerializer, SafeInfoResponseSerializer,
                          SafeModuleTransactionResponseSerializer,
                          SafeMultisigTransactionResponseSerializer,
                          SafeMultisigTransactionSerializer,
                          TransferResponseSerializer)
from .services import (BalanceServiceProvider, SafeServiceProvider,
                       TransactionServiceProvider)


class AboutView(APIView):
    """
    Returns info about the project.
    """
    renderer_classes = (JSONRenderer,)

    def get(self, request, format=None):
        content = {
            'name': 'Safe Transaction Service',
            'version': __version__,
            'api_version': self.request.version,
            'secure': self.request.is_secure(),
            'settings': {
                'ETHEREUM_NODE_URL': settings.ETHEREUM_NODE_URL,
                'ETHEREUM_TRACING_NODE_URL': settings.ETHEREUM_TRACING_NODE_URL,
                'ETH_INTERNAL_TXS_BLOCK_PROCESS_LIMIT ': settings.ETH_INTERNAL_TXS_BLOCK_PROCESS_LIMIT,
                'ETH_REORG_BLOCKS': settings.ETH_REORG_BLOCKS,
                'ETH_UNISWAP_FACTORY_ADDRESS': settings.ETH_UNISWAP_FACTORY_ADDRESS,
            }
        }
        return Response(content)


class AllTransactionsListView(ListAPIView):
    filter_backends = (django_filters.rest_framework.DjangoFilterBackend, OrderingFilter)
    pagination_class = SmallPagination

    def list(self, request, *args, **kwargs):
        transaction_service = TransactionServiceProvider()
        safe = self.kwargs['address']
        queryset = self.filter_queryset(transaction_service.get_all_tx_hashes(safe))
        page = self.paginate_queryset(queryset)

        if not page:
            return self.get_paginated_response({})

        all_tx_hashes = [element['safe_tx_hash'] for element in page]
        all_txs = transaction_service.get_all_txs_from_hashes(safe, all_tx_hashes)
        all_txs_serialized = transaction_service.serialize_all_txs(all_txs)
        return self.get_paginated_response(all_txs_serialized)

    @swagger_auto_schema(responses={400: 'Invalid data',
                                    404: 'Not found',
                                    422: 'Invalid ethereum address'})
    def get(self, request, *args, **kwargs):
        """
        Returns the history of a multisig tx (safe)
        """
        address = kwargs['address']
        # if not Web3.isChecksumAddress(address):
        #     return Response(status=status.HTTP_422_UNPROCESSABLE_ENTITY, data='Invalid ethereum address')

        response = super().get(request, *args, **kwargs)
        response.setdefault('ETag', 'W/' + hashlib.md5(str(response.data['results']).encode()).hexdigest())
        return response


class SafeModuleTransactionListView(ListAPIView):
    filter_backends = (django_filters.rest_framework.DjangoFilterBackend, OrderingFilter)
    ordering_fields = ['created']
    pagination_class = DefaultPagination
    serializer_class = SafeModuleTransactionResponseSerializer

    def get_queryset(self):
        return ModuleTransaction.objects.filter(
            safe=self.kwargs['address']
        ).select_related(
            'internal_tx__ethereum_tx'
        ).order_by(
            '-created'
        )

    @swagger_auto_schema(responses={400: 'Invalid data',
                                    404: 'Not found',
                                    422: 'Invalid ethereum address'})
    def get(self, request, address, format=None):
        """
        Returns the module transaction of a Safe
        """
        # if not Web3.isChecksumAddress(address):
        #     return Response(status=status.HTTP_422_UNPROCESSABLE_ENTITY, data='Invalid ethereum address')

        response = super().get(request, address)
        response.setdefault('ETag', 'W/' + hashlib.md5(str(response.data['results']).encode()).hexdigest())
        return response


@swagger_auto_schema(responses={200: 'Ok',
                                404: 'Not found'})
class SafeMultisigTransactionDetailView(RetrieveAPIView):
    serializer_class = SafeMultisigTransactionResponseSerializer
    lookup_field = 'safe_tx_hash'
    lookup_url_kwarg = 'safe_tx_hash'

    def get_queryset(self):
        return MultisigTransaction.objects.with_confirmations_required(
        ).prefetch_related(
            'confirmations'
        ).select_related(
            'ethereum_tx__block'
        )


class SafeMultisigTransactionListView(ListAPIView):
    filter_backends = (django_filters.rest_framework.DjangoFilterBackend, OrderingFilter)
    filterset_class = MultisigTransactionFilter
    ordering_fields = ['nonce', 'created']
    pagination_class = DefaultPagination

    def get_queryset(self):
        return MultisigTransaction.objects.filter(
            safe=self.kwargs['address']
        ).with_confirmations_required(
        ).prefetch_related(
            'confirmations'
        ).select_related(
            'ethereum_tx__block'
        ).order_by(
            '-nonce',
            '-created'
        )

    def get_unique_nonce(self, address: str):
        return MultisigTransaction.objects.filter(safe=address).distinct('nonce').count()

    def get_serializer_class(self):
        """
        Proxy returning a serializer class according to the request's verb.
        """
        if self.request.method == 'GET':
            return SafeMultisigTransactionResponseSerializer
        elif self.request.method == 'POST':
            return SafeMultisigTransactionSerializer

    @swagger_auto_schema(responses={400: 'Invalid data',
                                    404: 'Not found',
                                    422: 'Invalid ethereum address'})
    def get(self, request, *args, **kwargs):
        """
        Returns the history of a multisig tx (safe)
        """
        address = kwargs['address']
        # if not Web3.isChecksumAddress(address):
        #     return Response(status=status.HTTP_422_UNPROCESSABLE_ENTITY, data='Invalid ethereum address')

        response = super().get(request, *args, **kwargs)
        response.data['count_unique_nonce'] = self.get_unique_nonce(address) if response.data['count'] else 0
        response.setdefault('ETag', 'W/' + hashlib.md5(str(response.data['results']).encode()).hexdigest())
        return response

    @swagger_auto_schema(responses={201: 'Created or signature updated',
                                    400: 'Invalid data',
                                    422: 'Invalid ethereum address/User is not an owner or tx not approved/executed'})
    def post(self, request, address, format=None):
        """
        Creates a Multisig Transaction with its confirmations and retrieves all the information related.
        """
        # if not Web3.isChecksumAddress(address):
        #     return Response(status=status.HTTP_422_UNPROCESSABLE_ENTITY, data='Invalid ethereum address')

        request.data['safe'] = Web3.toChecksumAddress(address)
        serializer = self.get_serializer_class()(data=request.data)

        if not serializer.is_valid():
            return Response(status=status.HTTP_422_UNPROCESSABLE_ENTITY, data=serializer.errors)
        else:
            serializer.save()
            return Response(status=status.HTTP_201_CREATED)


class SafeBalanceView(APIView):
    serializer_class = SafeBalanceResponseSerializer

    @swagger_auto_schema(responses={200: SafeBalanceResponseSerializer(many=True),
                                    404: 'Safe not found',
                                    422: 'Safe address checksum not valid'})
    @method_decorator(cache_page(15))
    def get(self, request, address, format=None):
        """
        Get status of the safe
        """
        # if not Web3.isChecksumAddress(address):
        #     return Response(status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        # else:
        #     try:
        #         SafeContract.objects.get(address=address)
        #     except SafeContract.DoesNotExist:
        #         return Response(status=status.HTTP_404_NOT_FOUND)

        #     safe_balances = BalanceServiceProvider().get_balances(address)
        #     serializer = self.serializer_class(safe_balances, many=True)
        #     return Response(status=status.HTTP_200_OK, data=serializer.data)
        try:
            SafeContract.objects.get(address=address)
        except SafeContract.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        safe_balances = BalanceServiceProvider().get_balances(address)
        serializer = self.serializer_class(safe_balances, many=True)
        return Response(status=status.HTTP_200_OK, data=serializer.data)


class SafeBalanceUsdView(APIView):
    serializer_class = SafeBalanceUsdResponseSerializer

    @swagger_auto_schema(responses={200: SafeBalanceUsdResponseSerializer(many=True),
                                    404: 'Safe not found',
                                    422: 'Safe address checksum not valid'})
    @method_decorator(cache_page(15))
    def get(self, request, address, format=None):
        """
        Get status of the safe
        """
        # if not Web3.isChecksumAddress(address):
        #     return Response(status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        # else:
        #     try:
        #         SafeContract.objects.get(address=address)
        #     except SafeContract.DoesNotExist:
        #         return Response(status=status.HTTP_404_NOT_FOUND)

        #     safe_balances = BalanceServiceProvider().get_usd_balances(address)
        #     serializer = self.serializer_class(safe_balances, many=True)
        #     return Response(status=status.HTTP_200_OK, data=serializer.data)
        try:
            SafeContract.objects.get(address=address)
        except SafeContract.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        safe_balances = BalanceServiceProvider().get_usd_balances(address)
        serializer = self.serializer_class(safe_balances, many=True)
        return Response(status=status.HTTP_200_OK, data=serializer.data)


class SafeDelegateListView(ListCreateAPIView):
    pagination_class = DefaultPagination

    def get_queryset(self):
        return SafeContractDelegate.objects.filter(
            safe_contract_id=self.kwargs['address']
        )

    def get_serializer_class(self):
        if self.request.method == 'GET':
            return SafeDelegateResponseSerializer
        elif self.request.method == 'POST':
            return SafeDelegateSerializer

    @swagger_auto_schema(responses={400: 'Invalid data',
                                    404: 'Not found',
                                    422: 'Invalid Ethereum address'})
    def get(self, request, address, **kwargs):
        """
        Get the list of delegates for a Safe address
        """
        # if not Web3.isChecksumAddress(address):
        #     return Response(status=status.HTTP_422_UNPROCESSABLE_ENTITY, data='Invalid ethereum address')

        return super().get(request, address, **kwargs)

    @swagger_auto_schema(responses={202: 'Accepted',
                                    400: 'Malformed data',
                                    422: 'Invalid Ethereum address/Error processing data'})
    def post(self, request, address, **kwargs):
        """
        Create a delegate for a Safe address with a custom label. Calls with same delegate but different label or
        signer will update the label or delegator if different.
        For the signature we are using TOTP with `T0=0` and `Tx=3600`. TOTP is calculated by taking the
        Unix UTC epoch time (no milliseconds) and dividing by 3600 (natural division, no decimals)
        For signature this hash need to be signed: keccak(address + str(int(current_epoch // 3600)))
        For example:
             - we want to add the owner `0x132512f995866CcE1b0092384A6118EDaF4508Ff` and `epoch=1586779140`.
             - TOTP = epoch // 3600 = 1586779140 // 3600 = 440771
             - The hash to sign by a Safe owner would be `keccak("0x132512f995866CcE1b0092384A6118EDaF4508Ff440771")`
        """
        # if not Web3.isChecksumAddress(address):
        #     return Response(status=status.HTTP_422_UNPROCESSABLE_ENTITY, data='Invalid ethereum address')

        request.data['safe'] = address
        return super().post(request, address, **kwargs)


class SafeDelegateDestroyView(DestroyAPIView):
    serializer_class = SafeDelegateDeleteSerializer

    def get_object(self):
        return get_object_or_404(SafeContractDelegate,
                                 safe_contract_id=self.kwargs['address'],
                                 delegate=self.kwargs['delegate_address'])

    @swagger_auto_schema(responses={202: 'Accepted',
                                    400: 'Malformed data',
                                    422: 'Invalid Ethereum address/Error processing data'})
    def delete(self, request, address, delegate_address, *args, **kwargs):
        """
        Delete a delegate for a Safe. Signature is built the same way that for adding a delegate.
        Check `POST /delegates/`
        """
        # if not Web3.isChecksumAddress(address) or not Web3.isChecksumAddress(delegate_address):
        #     return Response(status=status.HTTP_422_UNPROCESSABLE_ENTITY, data='Invalid ethereum address')

        request.data['safe'] = address
        request.data['delegate_address'] = delegate_address
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return super().delete(request, address, delegate_address, *args, **kwargs)


class SafeTransferListView(ListAPIView):
    filter_backends = (django_filters.rest_framework.DjangoFilterBackend,)
    filterset_class = TransferListFilter
    serializer_class = TransferResponseSerializer
    pagination_class = DefaultPagination

    def list(self, request, *args, **kwargs):
        # Queryset must be already filtered, as we cannot filter a union
        # queryset = self.filter_queryset(self.get_queryset())

        queryset = self.get_queryset()
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def get_transfers(self, address: str):
        tokens_queryset = super().filter_queryset(InternalTx.objects.token_txs_for_address(address))
        ether_queryset = super().filter_queryset(InternalTx.objects.ether_txs_for_address(address))
        return InternalTx.objects.union_ether_and_token_txs(tokens_queryset, ether_queryset)

    def get_queryset(self):
        address = self.kwargs['address']
        return self.get_transfers(address)

    @swagger_auto_schema(responses={200: TransferResponseSerializer(many=True),
                                    404: 'Txs not found',
                                    422: 'Safe address checksum not valid'})
    def get(self, request, address, format=None):
        """
        Returns the history of a multisig tx (safe)
        """
        # if not Web3.isChecksumAddress(address):
        #     return Response(status=status.HTTP_422_UNPROCESSABLE_ENTITY, data='Invalid ethereum address')

        response = super().get(request, address)
        response.setdefault('ETag', 'W/' + hashlib.md5(str(response.data['results']).encode()).hexdigest())
        return response


class SafeIncomingTransferListView(SafeTransferListView):
    def get_transfers(self, address: str):
        # hack, events seem to be stored with ETH checksum
        address_eth_checksum = Web3.toChecksumAddress(address)
        tokens_queryset = super().filter_queryset(InternalTx.objects.token_incoming_txs_for_address(address_eth_checksum))
        ether_queryset = super().filter_queryset(InternalTx.objects.ether_incoming_txs_for_address(address))
        return InternalTx.objects.union_ether_and_token_txs(tokens_queryset, ether_queryset)


class SafeCreationView(APIView):
    serializer_class = SafeCreationInfoResponseSerializer

    @swagger_auto_schema(responses={200: serializer_class(),
                                    404: 'Safes not found for that owner',
                                    422: 'Owner address checksum not valid'})
    @method_decorator(cache_page(60 * 60))  # 1 hour
    def get(self, request, address, format=None):
        """
        Get status of the safe
        """
        # if not Web3.isChecksumAddress(address):
        #     return Response(status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        safe_creation_info = SafeServiceProvider().get_safe_creation_info(address)
        if not safe_creation_info:
            return Response(status=status.HTTP_404_NOT_FOUND)

        serializer = self.serializer_class(safe_creation_info)
        return Response(status=status.HTTP_200_OK, data=serializer.data)


class SafeInfoView(APIView):
    serializer_class = SafeInfoResponseSerializer

    @swagger_auto_schema(responses={200: serializer_class(),
                                    404: 'Safes not found for that owner',
                                    422: 'Owner address checksum not valid'})
    def get(self, request, address, format=None):
        """
        Get status of the safe
        """
        # if not Web3.isChecksumAddress(address):
        #     return Response(status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        if not SafeContract.objects.filter(address=address).exists():
            return Response(status=status.HTTP_404_NOT_FOUND)

        safe_info = SafeServiceProvider().get_safe_info(address)
        serializer = self.serializer_class(safe_info)
        return Response(status=status.HTTP_200_OK, data=serializer.data)


class OwnersView(APIView):
    serializer_class = OwnerResponseSerializer

    @swagger_auto_schema(responses={200: OwnerResponseSerializer(),
                                    404: 'Safes not found for that owner',
                                    422: 'Owner address checksum not valid'})
    def get(self, request, address, format=None):
        """
        Get status of the safe
        """
        # if not Web3.isChecksumAddress(address):
        #     return Response(status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        safes_for_owner = SafeStatus.objects.addresses_for_owner(address)
        if not safes_for_owner:
            return Response(status=status.HTTP_404_NOT_FOUND)

        serializer = self.serializer_class(data={'safes': safes_for_owner})
        assert serializer.is_valid()
        return Response(status=status.HTTP_200_OK, data=serializer.data)
