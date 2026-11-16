# Ethereum Contract Monitor

A Python command-line tool that watches Ethereum mainnet block by block and prints an alert for every newly deployed contract whose deployer holds at least **0.5 ETH**. Each alert includes the contract's token details, its Etherscan verification status, and where the deployer's ETH most recently came from.

It catches both **direct deployments** and contracts created **through factories** (`CREATE` / `CREATE2` inside another contract), which a simple transaction scan misses.

Standard library only: no `pip install` needed.

---

## Example output

```
============================================================================
NEW ETHEREUM CONTRACT
Observed:       2026-10-05T14:32:07+00:00
Name:           Example Token
Contract:       0x1234…abcd
Supply:         1000000000 EXT
Verification:   Not verified yet (at lookup time)

Sender:         0x9f8e…7c6d
Sender balance: 2.4137 ETH
Balance timing: End of deployment block, after gas costs
Creator/factory: 0x9f8e…7c6d

Observed funding source(s):
  0x28c6…1d60
Funding note:   Latest observed funding block: 21034512
                Excludes same-block funding; not proof of ownership.

Block:          21034567
Creation type:  CREATE
Contract link:  https://etherscan.io/address/0x1234…abcd
Transaction:    https://etherscan.io/tx/0x5a6b…9e0f
============================================================================
[Block 21034567] 1 qualifying deployment(s) | 0 block(s) behind
```

*(Addresses shortened for illustration.)*

---

## Features

- **Full deployment coverage**: traces every transaction in each block, so contracts created by factories and other contracts are detected, not only direct deployments.
- **Deployer filter**: only reports deployments whose sender holds at least 0.5 ETH at the end of the deployment block (configurable).
- **Token details**: reads `name`, `symbol`, `totalSupply` and `decimals` directly from the contract, and formats the supply exactly, without floating-point rounding.
- **Verification status**: checks on Etherscan whether the source code is verified.
- **Funding trace**: looks up the most recent incoming ETH transfers to the deployer (normal and internal transactions) before the deployment block.
- **Safety checks**: waits for confirmation blocks, detects chain reorganisations, retries on API errors without skipping blocks, and never prints URLs that contain your keys.

---

## Requirements

- **Python 3.10+**
- An **Etherscan API key** (free tier works): for verification status and funding history. Get one at [etherscan.io/apis](https://etherscan.io/apis).
- An **Ethereum mainnet RPC URL that supports `debug_traceBlockByNumber` with `callTracer`**: needed to detect factory deployments. Standard free RPC endpoints usually do **not** support this; a provider such as QuickNode with the debug/trace add-on does.

---

## Usage

```bash
python EthereumScreener.py
```

The script asks for your Etherscan key and RPC URL at startup (input is hidden). Alternatively, fill in the two constants at the top of the file, but **never commit a file containing your keys**.

Stop it with `Ctrl+C`.

---

## Settings

All settings are at the top of the script:

| Setting | Default | Meaning |
|---|---|---|
| `MIN_BALANCE_WEI` | 0.5 ETH | Minimum deployer balance for an alert |
| `CONFIRMATION_BLOCKS` | 2 | How far behind the chain head to stay, to reduce reorg risk |
| `POLL_SECONDS` | 3 | Wait between checks for new blocks |
| `ETHERSCAN_INTERVAL_SECONDS` | 0.4 | Spacing between Etherscan calls, to respect rate limits |
| `FUNDING_MAX_PAGES` | 5 | Maximum pages of history searched per deployer |
| `START_BLOCK` | `None` | `None` starts near the current head; a number replays from that block (if your RPC supports historical tracing) |

---

## How it works

1. **Follow the chain:** poll for new blocks, staying `CONFIRMATION_BLOCKS` behind the head.
2. **Trace the block:** call `debug_traceBlockByNumber` and walk each transaction's call tree, collecting every successful `CREATE` / `CREATE2`. Creations inside reverted calls are ignored.
3. **Filter:** check the sender's ETH balance at that block, and keep only deployments above the threshold.
4. **Enrich:** for each qualifying contract, read token metadata via `eth_call`, check verification on Etherscan, and search the sender's incoming ETH history.
5. **Verify and print:** confirm the block hash hasn't changed during the scan, then print the alerts.

---

## Limitations

- **Funding sources are a heuristic, not proof.** The tool reports who sent ETH to the deployer most recently before the deployment. It does not prove ownership, and it doesn't trace further back.
- **The balance filter applies to the transaction sender**, not to the original funder.
- **Token fields are best-effort:** non-token contracts, or tokens that don't follow ERC-20, show "Unavailable".
- **Verification is checked once, at alert time.** Contracts are often verified a few minutes later.
- **No persistence:** progress isn't saved between runs, and the monitor stops on a chain reorganisation so you can review earlier alerts.
- **Costs:** block tracing is a heavy RPC call, and paid providers charge credits for it.
- **Mainnet only.**

This is a research and monitoring tool. It is not financial advice, and an alert is not a signal that a contract is safe.

---

## Possible improvements

- Save progress and alerts to a file or SQLite database
- Telegram or Discord notifications
- Filters by token supply, verification or factory address
- Multi-hop funding tracing
- Support for other EVM chains (Base, Arbitrum)

---

## Built with

Python standard library only: `urllib`, `json`, `getpass`, `datetime`. Data from the Ethereum JSON-RPC API and the Etherscan v2 API.
