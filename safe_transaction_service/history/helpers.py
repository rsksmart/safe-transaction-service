import time

from eth_utils import keccak


class DelegateSignatureHelper:
    @classmethod
    def calculate_topt(cls, topt_tx: int = 3600, topt_t0: int = 0) -> int:
        """
        https://en.wikipedia.org/wiki/Time-based_One-time_Password_algorithm
        :param topt_tx:
        :param topt_t0:
        :return:
        """
        return int((time.time() - topt_t0) // topt_tx)

    @classmethod
    def calculate_hash(cls, address: str, eth_sign: bool = False) -> bytes:
        topt = cls.calculate_topt()
        message = address + str(topt)
        if eth_sign:
            return keccak(text="\x19Ethereum Signed Message:\n" + str(len(message)) + message)
        else:
            return keccak(text=message)
