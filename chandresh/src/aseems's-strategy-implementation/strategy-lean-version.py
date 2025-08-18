# BankNifty option short straddle with Supertrend filter
# Uses snake_case names throughout (Lean supports these aliases)

from AlgorithmImports import *
from datetime import time, timedelta


class Banknifty_option_selling_supertrend(QCAlgorithm):
    # ---------- 1. INITIALISATION ----------
    def initialize(self):
        self.set_start_date(2023, 1, 1)
        self.set_end_date(2023, 1, 15)
        self.set_cash(1_000_000)

        # --- user-tunable parameters ---
        self.entry_time = time(9, 19)        # can change in config
        self.exit_time  = time(15, 15)
        self.combined_sl_pct   = 0.25        # 25 %
        self.individual_sl_pct = 0.15        # 15 %
        self.supertrend_period = 10
        self.supertrend_mult   = 3

        # --- symbols ---
        self.index_symbol  = self.add_index("BANKNIFTY", Resolution.MINUTE).symbol
        self.option_chain  = self.add_index_option("BANKNIFTY", Resolution.MINUTE)
        self.option_chain.set_filter(-5, 5, timedelta(), timedelta(days=1))

        # --- state tracking ---
        self.ce = self.pe = None
        self.ce_entry = self.pe_entry = 0
        self.ce_sl_hit = self.pe_sl_hit = False

        # --- scheduled events ---
        self.schedule.on(
            self.date_rules.every_day(self.index_symbol),
            self.time_rules.at(self.entry_time.hour, self.entry_time.minute),
            self.entry_logic
        )
        self.schedule.on(
            self.date_rules.every_day(self.index_symbol),
            self.time_rules.at(self.exit_time.hour, self.exit_time.minute),
            self.exit_all
        )

    # ---------- 2. ENTRY ----------
    def entry_logic(self):
        if self.portfolio.invested:
            return

        chain = self.option_chain.option_chain_provider.get_option_contract_list(
            self.option_chain.symbol, self.time
        )
        if not chain:
            return

        spot     = self.securities["BANKNIFTY"].price
        atm_strike = round(spot / 100) * 100     # nearest 100 pts

        ce_contracts = [c for c in chain
                        if c.id.option_right == OptionRight.CALL
                        and c.id.strike_price == atm_strike]
        pe_contracts = [c for c in chain
                        if c.id.option_right == OptionRight.PUT
                        and c.id.strike_price == atm_strike]
        if not ce_contracts or not pe_contracts:
            self.debug("ATM contracts not found")
            return

        self.ce = self.add_option_contract(ce_contracts[0], Resolution.MINUTE).symbol
        self.pe = self.add_option_contract(pe_contracts[0], Resolution.MINUTE).symbol

        self.market_order(self.ce, -1)
        self.market_order(self.pe, -1)

        self.ce_entry = self.securities[self.ce].price
        self.pe_entry = self.securities[self.pe].price
        self.ce_sl_hit = self.pe_sl_hit = False

        self.debug(f"Short CE {self.ce} @ {self.ce_entry:.2f}; "
                   f"Short PE {self.pe} @ {self.pe_entry:.2f}")

    # ---------- 3. RUNTIME HANDLER ----------
    def on_data(self, data: Slice):
        if not self.portfolio.invested:
            return

        # --- check stop-loss for each leg ---
        if not self.ce_sl_hit and data.contains_key(self.ce):
            if data[self.ce].price > self.ce_entry * (1 + self.individual_sl_pct):
                self.liquidate(self.ce)
                self.ce_sl_hit = True
                self.debug("CE individual SL hit")

        if not self.pe_sl_hit and data.contains_key(self.pe):
            if data[self.pe].price > self.pe_entry * (1 + self.individual_sl_pct):
                self.liquidate(self.pe)
                self.pe_sl_hit = True
                self.debug("PE individual SL hit")

        # --- apply supertrend logic to surviving leg ---
        if self.ce_sl_hit and not self.pe_sl_hit:
            self.apply_supertrend_and_trail(self.pe)
        elif self.pe_sl_hit and not self.ce_sl_hit:
            self.apply_supertrend_and_trail(self.ce)

    # ---------- 4. SUPER-TREND & TRAILING SL ----------
    def apply_supertrend_and_trail(self, symbol: Symbol):
        price = self.securities[symbol].price
        supertrend_val = self.calculate_dummy_supertrend(symbol)

        # (a) price above supertrend ⇒ keep 25 % SL unchanged
        # (b) price below supertrend ⇒ tighten/trail SL
        # (simple demo: set SL to 10 % if below ST; replace with your own trailing logic)
        if price < supertrend_val:
            new_sl = self.securities[symbol].holdings.average_price * (1 + 0.10)
            self.update_stop_loss(symbol, new_sl)
        else:
            # exit if price now crosses above supertrend again
            self.liquidate(symbol)
            self.debug(f"Exit {symbol} as close > supertrend")

    def calculate_dummy_supertrend(self, symbol: Symbol) -> float:
        """Placeholder; replace with real Supertrend calculation."""
        return self.securities[symbol].price * 0.97

    def update_stop_loss(self, symbol: Symbol, new_sl_price: float):
        """Cancels existing SL orders and places a new one at new_sl_price."""
        for order in self.transactions.get_open_orders(symbol):
            if order.tag == "SL":
                self.transactions.cancel_order(order.id)
        self.stop_market_order(symbol, -self.portfolio[symbol].quantity, new_sl_price, tag="SL")
        self.debug(f"Trailing SL updated to {new_sl_price:.2f} for {symbol}")

    # ---------- 5. DAY-END EXIT ----------
    def exit_all(self):
        self.liquidate()
        self.debug("All positions closed at 15:15")