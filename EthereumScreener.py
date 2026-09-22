"""
Ethereum contract monitor — terminal output only.
Python 3.10+. No pip packages or Telegram keys needed.

Paste your keys below, or leave them blank to enter them when the script starts.

IMPORTANT:
An Etherscan key provides funding history and verification status.
You ALSO need an Ethereum mainnet RPC URL supporting
debug_traceBlockByNumber with callTracer to detect factory deployments.

The 0.5 ETH filter applies to the deployment transaction's sender,
measured at the end of the deployment block.
"""

import getpass
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


# ---------------------- SETTINGS ----------------------

ETHERSCAN_API_KEY = ""
ETHEREUM_RPC_URL = ""

MIN_BALANCE_WEI = 500_000_000_000_000_000  # Exactly 0.5 ETH
CONFIRMATION_BLOCKS = 2
POLL_SECONDS = 3
ETHERSCAN_INTERVAL_SECONDS = 0.4
FUNDING_MAX_PAGES = 5

# None starts near the current head. Historical reads require RPC support.
START_BLOCK = None

# ------------------------------------------------------


class APIError(Exception):
    pass


_last_etherscan_request = 0.0


def request_json(url, payload=None):
    data = None if payload is None else json.dumps(payload).encode()

    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "EthereumTerminalMonitor/1.0",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise APIError(f"HTTP {error.code}") from None
    except (OSError, ValueError):
        # Avoid printing URLs that contain API keys.
        raise APIError("Network error or invalid JSON response") from None


def rpc(method, params):
    response = request_json(
        ETHEREUM_RPC_URL,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        },
    )

    if not isinstance(response, dict):
        raise APIError(f"{method}: unexpected response")

    if response.get("error") or "result" not in response:
        error = response.get("error") or {}
        code = error.get("code", "unknown")
        raise APIError(
            f"{method} failed, code {code}. "
            "Check the RPC URL, tracing support and quota."
        )

    return response["result"]


def get_block(number, full_transactions=False):
    block = rpc(
        "eth_getBlockByNumber",
        [hex(number), full_transactions],
    )

    if not block:
        raise APIError(f"Block {number} is unavailable")

    return block


def etherscan(module, action, **parameters):
    global _last_etherscan_request

    delay = (
        _last_etherscan_request
        + ETHERSCAN_INTERVAL_SECONDS
        - time.monotonic()
    )
    if delay > 0:
        time.sleep(delay)

    _last_etherscan_request = time.monotonic()

    query = urllib.parse.urlencode(
        {
            "chainid": 1,
            "module": module,
            "action": action,
            "apikey": ETHERSCAN_API_KEY,
            **parameters,
        }
    )

    response = request_json(f"https://api.etherscan.io/v2/api?{query}")

    if not isinstance(response, dict):
        raise APIError(f"Etherscan {action}: unexpected response")

    if (
        response.get("status") == "1"
        and isinstance(response.get("result"), list)
    ):
        return response["result"]

    if response.get("message", "").lower() == "no transactions found":
        return []

    raise APIError(
        f"Etherscan {action} unavailable. "
        "Check your API key, quota and endpoint access."
    )


def format_units(value, decimals):
    """Format integers exactly, without floating-point rounding."""
    whole, remainder = divmod(value, 10**decimals)

    if remainder == 0:
        return str(whole)

    return f"{whole}.{remainder:0{decimals}d}".rstrip("0")


def clean_text(value):
    return "".join(
        character for character in str(value)
        if character.isprintable()
    )[:160]


def decode_contract_text(raw):
    data = bytes.fromhex(raw.removeprefix("0x"))

    if len(data) == 32:
        # Some older contracts return bytes32 instead of string.
        value = data.rstrip(b"\x00")
    elif len(data) >= 64:
        offset = int.from_bytes(data[:32], "big")

        if offset < 32 or offset + 32 > len(data):
            raise ValueError("Invalid string offset")

        length = int.from_bytes(data[offset:offset + 32], "big")

        if length > 4096 or offset + 32 + length > len(data):
            raise ValueError("Invalid string length")

        value = data[offset + 32:offset + 32 + length]
    else:
        raise ValueError("No readable string")

    return clean_text(value.decode("utf-8", errors="replace"))


def contract_metadata(address, block_number):
    selectors = {
        "name": "06fdde03",
        "symbol": "95d89b41",
        "supply": "18160ddd",
        "decimals": "313ce567",
    }
    metadata = {}

    for field, selector in selectors.items():
        try:
            result = rpc(
                "eth_call",
                [
                    {
                        "to": address,
                        "data": "0x" + selector,
                        "gas": hex(200_000),
                    },
                    hex(block_number),
                ],
            )

            if field in ("name", "symbol"):
                metadata[field] = decode_contract_text(result)
            elif isinstance(result, str) and len(result) == 66:
                metadata[field] = int(result, 16)

        except (APIError, ValueError, TypeError, AttributeError):
            # General contracts may not implement token methods.
            continue

    return metadata


def verification_status(address):
    try:
        rows = etherscan(
            "contract",
            "getsourcecode",
            address=address,
        )

        if len(rows) != 1 or "SourceCode" not in rows[0]:
            raise APIError("Unexpected verification response")

        contract = rows[0]
        verified = bool(contract["SourceCode"].strip())

        return (
            "Verified" if verified else "Not verified yet",
            clean_text(contract.get("ContractName", "")),
        )

    except APIError:
        return "Unknown — Etherscan lookup unavailable", ""


def funding_sources(address, deployment_block):
    """
    Find incoming ETH before the deployment block.

    Report sources in the latest observed funding block. This is not proof
    of ownership or the original funder. Same-block funding is excluded.
    """
    if deployment_block <= 0:
        return [], "No earlier blocks to search"

    matches = []
    incomplete = False

    try:
        for action in ("txlist", "txlistinternal"):
            found = []

            for page in range(1, FUNDING_MAX_PAGES + 1):
                rows = etherscan(
                    "account",
                    action,
                    address=address,
                    startblock=0,
                    endblock=deployment_block - 1,
                    page=page,
                    offset=100,
                    sort="desc",
                )

                for row in rows:
                    if (
                        row.get("to", "").lower() == address.lower()
                        and row.get("from", "").lower() != address.lower()
                        and int(row.get("value", "0")) > 0
                        and row.get("isError", "0") == "0"
                        and row.get("txreceipt_status", "1") != "0"
                    ):
                        found.append(row)

                if len(rows) < 100:
                    break

                if found:
                    newest = max(int(row["blockNumber"]) for row in found)
                    if int(rows[-1]["blockNumber"]) < newest:
                        break
            else:
                incomplete = True

            matches.extend(found)

    except (APIError, ValueError, KeyError, TypeError):
        return [], "Unknown — funding lookup unavailable"

    if not matches:
        note = "No incoming ETH found before the deployment block"
        if incomplete:
            note += " (search limit reached)"
        return [], note

    newest = max(int(row["blockNumber"]) for row in matches)

    sources = sorted(
        {
            row["from"].lower()
            for row in matches
            if int(row["blockNumber"]) == newest
        }
    )

    note = f"Latest observed funding block: {newest}"

    if len(sources) > 1:
        note += " — multiple possible funding sources"
    if incomplete:
        note += " — search incomplete"

    return sources, note


def successful_creations(frame):
    """Ignore failed creations and all descendants of reverted calls."""
    if not isinstance(frame, dict) or "type" not in frame:
        raise APIError("Malformed callTracer frame")

    if frame.get("error"):
        return

    if frame["type"].upper() in ("CREATE", "CREATE2") and frame.get("to"):
        yield frame

    for child in frame.get("calls", []):
        yield from successful_creations(child)


def find_deployments(block):
    number = int(block["number"], 16)

    traces = rpc(
        "debug_traceBlockByNumber",
        [
            hex(number),
            {
                "tracer": "callTracer",
                "timeout": "30s",
                "tracerConfig": {"onlyTopCall": False},
            },
        ],
    )

    transactions = block["transactions"]

    if not isinstance(traces, list) or len(traces) != len(transactions):
        raise APIError("Incomplete block trace; retrying without skipping")

    deployments = []
    seen = set()
    balances = {}

    for transaction, trace in zip(transactions, traces):
        if (
            not isinstance(trace, dict)
            or trace.get("error")
            or not isinstance(trace.get("result"), dict)
        ):
            raise APIError("Transaction trace failed; block will be retried")

        if (
            trace.get("txHash", transaction["hash"]).lower()
            != transaction["hash"].lower()
        ):
            raise APIError("Trace transaction order mismatch")

        for creation in successful_creations(trace["result"]):
            address = creation["to"].lower()
            sender = transaction["from"].lower()
            identifier = (transaction["hash"], address)

            if identifier in seen:
                continue
            seen.add(identifier)

            if sender not in balances:
                balances[sender] = int(
                    rpc("eth_getBalance", [sender, hex(number)]),
                    16,
                )

            if balances[sender] < MIN_BALANCE_WEI:
                continue

            deployments.append(
                {
                    "address": address,
                    "sender": sender,
                    "creator": creation["from"].lower(),
                    "balance": balances[sender],
                    "transaction": transaction["hash"],
                    "kind": creation["type"].upper(),
                    "block": number,
                }
            )

    return deployments


def print_deployment(deployment, funding_cache):
    address = deployment["address"]
    number = deployment["block"]

    metadata = contract_metadata(address, number)
    verification, source_name = verification_status(address)

    sender = deployment["sender"]
    if sender not in funding_cache:
        funding_cache[sender] = funding_sources(sender, number)

    sources, funding_note = funding_cache[sender]

    name = metadata.get("name") or source_name or "Unavailable"
    supply = "Unavailable — unsupported method or failed call"

    if "supply" in metadata:
        decimals = metadata.get("decimals")

        if decimals is not None and 0 <= decimals <= 255:
            supply = format_units(metadata["supply"], decimals)
            if metadata.get("symbol"):
                supply += " " + metadata["symbol"]
        else:
            supply = f"{metadata['supply']} raw units (decimals unavailable)"

    print("\n" + "=" * 76)
    print("NEW ETHEREUM CONTRACT")
    print(f"Observed:       {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    print(f"Name:           {name}")
    print(f"Contract:       {address}")
    print(f"Supply:         {supply}")
    print(f"Verification:   {verification} (at lookup time)")
    print()
    print(f"Sender:         {sender}")
    print(f"Sender balance: {format_units(deployment['balance'], 18)} ETH")
    print("Balance timing: End of deployment block, after gas costs")
    print(f"Creator/factory:{' '}{deployment['creator']}")
    print()
    print("Observed funding source(s):")
    if sources:
        for source in sources:
            print(f"  {source}")
    else:
        print("  Unknown")
    print(f"Funding note:   {funding_note}")
    print("                Excludes same-block funding; not proof of ownership.")
    print()
    print(f"Block:          {number}")
    print(f"Creation type:  {deployment['kind']}")
    print(f"Contract link:  https://etherscan.io/address/{address}")
    print(f"Transaction:    https://etherscan.io/tx/{deployment['transaction']}")
    print("=" * 76, flush=True)


def main():
    global ETHERSCAN_API_KEY, ETHEREUM_RPC_URL

    if not ETHERSCAN_API_KEY.strip():
        ETHERSCAN_API_KEY = getpass.getpass("Etherscan API key: ").strip()

    if not ETHEREUM_RPC_URL.strip():
        ETHEREUM_RPC_URL = getpass.getpass(
            "Ethereum mainnet HTTPS RPC URL with block tracing: "
        ).strip()

    if not ETHERSCAN_API_KEY or not ETHEREUM_RPC_URL:
        raise APIError("Both the Etherscan key and Ethereum RPC URL are required")

    if int(rpc("eth_chainId", []), 16) != 1:
        raise APIError("Wrong network: use Ethereum mainnet")

    head = int(rpc("eth_blockNumber", []), 16)
    next_block = (
        START_BLOCK
        if START_BLOCK is not None
        else max(0, head - CONFIRMATION_BLOCKS)
    )

    previous_number = None
    previous_hash = None

    print("\nEthereum monitor started — terminal output only.")
    print(f"Minimum sender balance: {format_units(MIN_BALANCE_WEI, 18)} ETH")
    print(f"Starting block: {next_block}")
    print("Coverage: direct deployments and factory CREATE/CREATE2.")
    print("Press Ctrl+C to stop.")
    print("This test version does not save progress between runs.\n", flush=True)

    while True:
        try:
            if previous_number is not None:
                if get_block(previous_number)["hash"] != previous_hash:
                    print(
                        "\nChain reorganization detected. Monitor stopped; "
                        "earlier printed alerts may no longer be canonical."
                    )
                    return

            head = int(rpc("eth_blockNumber", []), 16)
            target = max(0, head - CONFIRMATION_BLOCKS)

            if next_block > target:
                time.sleep(POLL_SECONDS)
                continue

            block = get_block(next_block, full_transactions=True)

            if previous_hash is not None and block["parentHash"] != previous_hash:
                print("\nChain reorganization detected. Restart after review.")
                return

            # Finish detection and balance checks before printing any alerts.
            deployments = find_deployments(block)

            if get_block(next_block)["hash"] != block["hash"]:
                raise APIError("Block changed during scan; retrying")

            funding_cache = {}
            for deployment in deployments:
                print_deployment(deployment, funding_cache)

            print(
                f"[Block {next_block}] "
                f"{len(deployments)} qualifying deployment(s) | "
                f"{target - next_block} block(s) behind",
                flush=True,
            )

            previous_number = next_block
            previous_hash = block["hash"]
            next_block += 1

        except APIError as error:
            print(f"[Retry in 10 seconds] {error}", flush=True)
            time.sleep(10)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nMonitor stopped.")
    except APIError as error:
        print(f"\nUnable to start: {error}")