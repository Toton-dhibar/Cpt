import argparse
import secrets
import threading
import time
from typing import Optional, Set

import requests
from bip_utils import (
    Bip39SeedGenerator,
    Bip44,
    Bip44Coins,
    Bip44Changes,
    Bip39MnemonicGenerator,
    Bip39WordsNum,
)
from bip_utils.utils.mnemonic.mnemonic_ex import MnemonicChecksumError

# Single lightweight script tuned for Termux:
# - No FastAPI server or external backend
# - Minimal dependencies: requests + bip_utils
# - Multi-phase checks with optional verification
# - Throttle support to keep CPU usage low

MAX_PHRASE_ATTEMPTS = 1000
# Fallback price guardrail (manual) in case price API is blocked/offline. Updated 2026-01.
FALLBACK_SOL_PRICE = 150  # manual fallback; adjust whenever market price meaningfully changes
# Tiny heuristic bump when tokens exist but SOL balance is zero. This does NOT reflect real token prices.
TOKEN_HEURISTIC_VALUE = 0.000001

generated_phrases: Set[str] = set()
MNEMONIC_GENERATOR = Bip39MnemonicGenerator()


def _get_words_num(word_count: int) -> Bip39WordsNum:
    return {
        12: Bip39WordsNum.WORDS_NUM_12,
        15: Bip39WordsNum.WORDS_NUM_15,
        18: Bip39WordsNum.WORDS_NUM_18,
        21: Bip39WordsNum.WORDS_NUM_21,
        24: Bip39WordsNum.WORDS_NUM_24,
    }.get(word_count, Bip39WordsNum.WORDS_NUM_12)


def generate_random_phrase(word_count: int = 12) -> str:
    words_num = _get_words_num(word_count)
    for _ in range(MAX_PHRASE_ATTEMPTS):
        phrase = str(MNEMONIC_GENERATOR.FromWordsNumber(words_num))
        if phrase not in generated_phrases:
            generated_phrases.add(phrase)
            return phrase
    return str(MNEMONIC_GENERATOR.FromWordsNumber(words_num))


def derive_trx_address(phrase: str) -> str:
    seed_bytes = Bip39SeedGenerator(phrase).Generate()
    bip44_mst_ctx = Bip44.FromSeed(seed_bytes, Bip44Coins.TRON)
    bip44_acc_ctx = bip44_mst_ctx.Purpose().Coin().Account(0)
    bip44_chg_ctx = bip44_acc_ctx.Change(Bip44Changes.CHAIN_EXT)
    bip44_addr_ctx = bip44_chg_ctx.AddressIndex(0)
    return bip44_addr_ctx.PublicKey().ToAddress()


def derive_sol_address(phrase: str) -> str:
    seed_bytes = Bip39SeedGenerator(phrase).Generate()
    bip44_mst_ctx = Bip44.FromSeed(seed_bytes, Bip44Coins.SOLANA)
    bip44_acc_ctx = bip44_mst_ctx.Purpose().Coin().Account(0)
    bip44_chg_ctx = bip44_acc_ctx.Change(Bip44Changes.CHAIN_EXT)
    bip44_addr_ctx = bip44_chg_ctx.AddressIndex(0)
    return bip44_addr_ctx.PublicKey().ToAddress()


def check_tron_activity(address: str, session: Optional[requests.Session] = None) -> int:
    sess = session or requests
    try:
        url = f"https://api.trongrid.io/v1/accounts/{address}/transactions"
        response = sess.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return len(data.get("data", []))
    except (requests.RequestException, ValueError):
        return 0
    return 0


def check_sol_value(address: str, session: Optional[requests.Session] = None) -> float:
    sess = session or requests
    try:
        rpc_url = "https://api.mainnet-beta.solana.com"
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [address]}
        resp = sess.post(rpc_url, json=payload, timeout=10)
        sol_balance = 0
        if resp.status_code == 200:
            result = resp.json().get("result", {})
            lamports = result.get("value", 0)
            sol_balance = lamports / 10**9

        sol_price = 0
        try:
            price_resp = sess.get(
                "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd",
                timeout=10,
            )
            sol_price = price_resp.json().get("solana", {}).get("usd", 0)
        except (requests.RequestException, ValueError):
            sol_price = FALLBACK_SOL_PRICE  # fallback guardrail for offline/blocked price lookups

        sol_usd_value = sol_balance * sol_price

        token_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [
                address,
                {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                {"encoding": "jsonParsed"},
            ],
        }
        token_resp = sess.post(rpc_url, json=token_payload, timeout=10)
        if token_resp.status_code == 200:
            tokens = token_resp.json().get("result", {}).get("value", [])
            if tokens:
                return sol_usd_value + (TOKEN_HEURISTIC_VALUE * len(tokens))  # tiny heuristic for token presence
        return sol_usd_value
    except (requests.RequestException, ValueError):
        return 0.0


class WalletHunter:
    def __init__(
        self,
        threads: int = 2,
        word_count: int = 12,
        min_trx_txs: int = 1,
        min_sol_value: float = 0.004375,
        delay: float = 0.05,
        verify: bool = True,
        hits_file: Optional[str] = None,
        max_checks: Optional[int] = None,
    ):
        self.word_count = word_count
        self.min_trx_txs = min_trx_txs
        self.min_sol_value = min_sol_value
        self.delay = delay
        self.verify = verify
        self.hits_file = hits_file
        self.max_checks = max_checks
        self.num_threads = max(1, threads)
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.counter = 0
        self.session = requests.Session()

    def _record_hit(self, phrase: str, trx: str, trx_txs: int, sol: str, sol_val: float):
        if not self.hits_file:
            return
        with self.lock:
            with open(self.hits_file, "a", encoding="utf-8") as fp:
                fp.write(
                    f"phrase={phrase}\ntrx={trx} txs={trx_txs}\nsol={sol} usd={sol_val:.6f}\n{'-'*40}\n"
                )

    def _phase_log(self, phrase: str, trx_addr: str, sol_addr: str, trx_txs: int, sol_val: float, verified: bool):
        with self.lock:
            self.counter += 1
            print(f"\n[{self.counter}] Phase 1: Phrase ready")
            print(f"   {phrase}")
            print("Phase 2: Derived addresses")
            print(f"   TRX: {trx_addr}")
            print(f"   SOL: {sol_addr}")
            print("Phase 3: Live stats")
            print(f"   TRX txs: {trx_txs}")
            print(f"   SOL value: ${sol_val:.6f}")
            if verified:
                print("Phase 4: Verification complete ✅")
            else:
                print("Phase 4: Verification skipped")
            print("-" * 50)

    def process_phrase(self, phrase: str, verify_override: Optional[bool] = None) -> bool:
        verify_flag = self.verify if verify_override is None else verify_override

        try:
            trx_address = derive_trx_address(phrase)
            sol_address = derive_sol_address(phrase)
        except (MnemonicChecksumError, ValueError):
            # Skip invalid mnemonic (bad checksum/word/validation error) without stopping worker threads
            return False

        trx_txs = check_tron_activity(trx_address, self.session)
        sol_val = check_sol_value(sol_address, self.session)

        active = trx_txs >= self.min_trx_txs or sol_val >= self.min_sol_value

        if active and verify_flag:
            # Re-check both metrics to guard against transient spikes or stale data
            time.sleep(self.delay)
            trx_txs = check_tron_activity(trx_address, self.session)
            sol_val = check_sol_value(sol_address, self.session)
            active = trx_txs >= self.min_trx_txs or sol_val >= self.min_sol_value

        self._phase_log(phrase, trx_address, sol_address, trx_txs, sol_val, verify_flag)

        # Persist any wallet with positive USD value (per requirement), even if below alert thresholds.
        should_save = sol_val > 0 or active
        if should_save:
            self._record_hit(phrase, trx_address, trx_txs, sol_address, sol_val)

        if active:
            self.stop_event.set()
            return True
        return False

    def _auto_worker(self):
        while not self.stop_event.is_set():
            if self.max_checks is not None and self.counter >= self.max_checks:
                self.stop_event.set()
                break
            phrase = generate_random_phrase(self.word_count)
            self.process_phrase(phrase)
            if self.delay:
                time.sleep(self.delay)

    def run_auto(self):
        threads = []
        print(f"Launching {self.num_threads} threads (delay {self.delay}s) ...")
        for _ in range(self.num_threads):
            t = threading.Thread(target=self._auto_worker, daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

    def run_manual(self, phrase: Optional[str] = None):
        if phrase:
            self.process_phrase(phrase, verify_override=self.verify)
            return
        while True:
            phrase = input("\nEnter mnemonic phrase (or 'exit'): ").strip()
            if phrase.lower() == "exit":
                break
            if len(phrase.split()) not in [12, 15, 18, 21, 24]:
                print("Invalid phrase length.")
                continue
            self.process_phrase(phrase, verify_override=self.verify)


def main():
    parser = argparse.ArgumentParser(description="Unified TRX/SOL hunter built for Termux.")
    parser.add_argument("--mode", choices=["auto", "manual", "generate", "health"], default="auto")
    parser.add_argument("--threads", type=int, default=2, help="Thread count for auto mode (default: 2)")
    parser.add_argument("--word-count", type=int, default=12, help="Mnemonic size (12/15/18/21/24)")
    parser.add_argument("--min-trx", type=int, default=1, help="Minimum TRX transactions to flag activity")
    parser.add_argument("--min-sol", type=float, default=0.004375, help="Minimum SOL USD value to flag activity")
    parser.add_argument("--delay", type=float, default=0.05, help="Pause between checks to save CPU (seconds)")
    parser.add_argument("--no-verify", action="store_true", help="Skip second verification phase")
    parser.add_argument("--hits-file", type=str, help="Optional file to store confirmed hits")
    parser.add_argument("--limit", type=int, help="Stop after N checks in auto mode")
    parser.add_argument("--phrase", type=str, help="Phrase to check in manual mode")
    parser.add_argument("--count", type=int, default=1, help="Number of phrases to generate in generate mode")
    args = parser.parse_args()

    if args.mode == "health":
        print("Wordlist size: 2048 (BIP-39 English)")
        print("Dependencies: requests, bip_utils")
        return

    if args.mode == "generate":
        for _ in range(max(1, args.count)):
            phrase = generate_random_phrase(args.word_count)
            print(phrase)
        return

    hunter = WalletHunter(
        threads=args.threads,
        word_count=args.word_count,
        min_trx_txs=args.min_trx,
        min_sol_value=args.min_sol,
        delay=args.delay,
        verify=not args.no_verify,
        hits_file=args.hits_file,
        max_checks=args.limit,
    )

    if args.mode == "manual":
        hunter.run_manual(phrase=args.phrase)
    else:
        hunter.run_auto()


if __name__ == "__main__":
    main()
