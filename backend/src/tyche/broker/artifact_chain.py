"""Broker adapter that serves pre-built flatfile/tradier chain artifacts."""

from __future__ import annotations

from datetime import date, datetime

from tyche.broker.tradier.symbols import normalize_option_type
from tyche.broker.base import (
    AccountBalance,
    BrokerOrder,
    BrokerPosition,
    CancelConfirmation,
    OptionContract,
    OptionsChain,
    OrderConfirmation,
    OrderPreview,
    OrderRequest,
    Quote,
)


class ArtifactChainBroker:
    """Serve scanner chains from ``options_chain_contracts.parquet`` rows."""

    def __init__(
        self,
        *,
        quotes: dict[str, Quote],
        expirations_by_symbol: dict[str, list[str]],
        chains_by_key: dict[tuple[str, str], OptionsChain],
        available_cash: float = 1_000_000.0,
    ) -> None:
        self._quotes = quotes
        self._expirations = expirations_by_symbol
        self._chains = chains_by_key
        self._available_cash = available_cash

    async def get_account_balances(self) -> AccountBalance:
        cash = self._available_cash
        return AccountBalance(
            cash=cash,
            buying_power=cash,
            net_liquidation_value=cash,
            market_value=0.0,
            total_equity=cash,
            open_pl=0.0,
            close_pl=0.0,
            pending_cash=0.0,
        )

    async def get_positions(self) -> list[BrokerPosition]:
        return []

    async def get_open_orders(self) -> list[BrokerOrder]:
        return []

    async def get_quote(self, symbol: str) -> Quote:
        return self._quotes.get(
            symbol,
            Quote(
                symbol=symbol,
                last=0.0,
                bid=0.0,
                ask=0.0,
                high=0.0,
                low=0.0,
                open=0.0,
                close=0.0,
                volume=0,
                change=0.0,
                change_pct=0.0,
            ),
        )

    async def get_quotes(self, symbols: list[str]) -> list[Quote]:
        return [await self.get_quote(symbol) for symbol in symbols]

    async def get_options_expirations(self, symbol: str) -> list[str]:
        return list(self._expirations.get(symbol, []))

    async def get_options_chain(
        self,
        symbol: str,
        expiration: str,
        greeks: bool = True,
    ) -> OptionsChain:
        chain = self._chains.get((symbol, expiration))
        if chain is not None:
            return chain
        quote = await self.get_quote(symbol)
        exp_date = datetime.strptime(expiration, "%Y-%m-%d").date()
        return OptionsChain(
            symbol=symbol,
            expiration=exp_date,
            underlying_price=quote.last,
            contracts=[],
        )

    async def preview_order(self, order: OrderRequest) -> OrderPreview:
        raise NotImplementedError("ArtifactChainBroker is read-only")

    async def place_order(self, order: OrderRequest) -> OrderConfirmation:
        raise NotImplementedError("ArtifactChainBroker is read-only")

    async def cancel_order(self, broker_order_id: str) -> CancelConfirmation:
        raise NotImplementedError("ArtifactChainBroker is read-only")


def build_artifact_chain_broker(
    contract_rows: list[dict],
    *,
    quotes: dict[str, Quote],
    available_cash: float,
) -> ArtifactChainBroker:
    """Index flat contract rows into broker-shaped chains."""
    expirations_by_symbol: dict[str, set[str]] = {}
    chains_by_key: dict[tuple[str, str], OptionsChain] = {}

    for row in contract_rows:
        ticker = str(row.get("ticker") or "")
        if not ticker:
            continue
        exp_raw = row.get("expiration")
        if isinstance(exp_raw, date):
            exp_str = exp_raw.isoformat()
        else:
            exp_str = str(exp_raw or "")[:10]
        if not exp_str:
            continue

        expirations_by_symbol.setdefault(ticker, set()).add(exp_str)
        key = (ticker, exp_str)
        if key not in chains_by_key:
            quote = quotes.get(ticker)
            underlying = float(
                quote.last if quote else row.get("underlying_price") or 0.0
            )
            chains_by_key[key] = OptionsChain(
                symbol=ticker,
                expiration=datetime.strptime(exp_str, "%Y-%m-%d").date(),
                underlying_price=underlying,
                contracts=[],
            )

        bid = float(row.get("bid") or row.get("close") or 0.0)
        ask = float(row.get("ask") or bid)
        mid = float(row.get("mid") or ((bid + ask) / 2 if bid and ask else bid))
        exp_date = chains_by_key[key].expiration
        chains_by_key[key].contracts.append(
            OptionContract(
                option_symbol=str(
                    row.get("option_symbol") or row.get("option_ticker") or ""
                ),
                option_type=normalize_option_type(
                    str(row.get("option_type") or "put")
                ),
                strike=float(row.get("strike") or 0.0),
                expiration=exp_date,
                bid=bid,
                ask=ask,
                mid=mid,
                last=float(row.get("last") or mid),
                volume=int(row.get("volume") or 0),
                open_interest=int(row.get("open_interest") or 0),
                implied_volatility=float(row.get("implied_volatility") or 0.0),
                delta=float(row.get("delta") or 0.0),
                theta=float(row.get("theta") or 0.0),
            )
        )

    sorted_expirations = {
        ticker: sorted(values) for ticker, values in expirations_by_symbol.items()
    }
    return ArtifactChainBroker(
        quotes=quotes,
        expirations_by_symbol=sorted_expirations,
        chains_by_key=chains_by_key,
        available_cash=available_cash,
    )
