# banknifty_straddle_supertrend.py
from datetime import time
from nautilus_trader.indicators.supertrend import SuperTrend
from nautilus_trader.core.uuid import uuid32
from nautilus_trader.model.objects import Money
from nautilus_trader.model.order import OrderSide, OrderType
from nautilus_trader.model.data import PriceBar
from nautilus_trader.event.stream import EventStream
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.strategy.strategy import Strategy


class BankniftyStraddleSupertrend(Strategy):
    """
    Short CE + PE at 09:19, manage legs with SL and SuperTrend on 3-min bars.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # ---- USER PARAMETERS ----
        self.entry_time = time(9, 19)
        self.exit_time  = time(15, 15)
        self.combined_sl = 0.25          # 25 %
        self.leg_sl      = 0.15          # 15 %
        self.st_period   = 10
        self.st_mult     = 3

        # ---- STATE ----
        self.ce_inst = None              # InstrumentId for CE
        self.pe_inst = None              # InstrumentId for PE
        self.entry_prices = {}
        self.leg_hit_sl   = {}

        # SuperTrend indicator for surviving leg (3-min bars)
        self.supertrend = SuperTrend(
            period=self.st_period,
            multiplier=self.st_mult,
        )

    # ─────────────────────────────────────────────────────────────
    # HOOKS
    # ─────────────────────────────────────────────────────────────
    def on_start(self, event):
        self.log_info("Strategy started")

    def on_bar_data(self, event: PriceBar):
        """
        Runs on every bar (subscribe() sets 3-minute resolution).
        """
        now = self.clock.time()

        # Entry block – only once per day at 09:19
        if now == self.entry_time and not self.position:
            self._enter_short_straddle(bar=event)

        # Intraday management
        if self.position:
            self._check_leg_stop_loss()
            self._apply_supertrend(bar=event)

        # Hard exit at 15:15
        if now >= self.exit_time:
            self.close_all_orders()
            self.flat()
            self.log_info("Day-end exit executed")

    # ─────────────────────────────────────────────────────────────
    #  INTERNAL HELPERS
    # ─────────────────────────────────────────────────────────────
    def _enter_short_straddle(self, bar: PriceBar):
        """
        Sells 1 lot ATM CE + PE based on current spot price.
        """
        spot = bar.close
        atm_strike = round(spot / 100) * 100

        self.ce_inst = self._find_option('CALL', atm_strike)
        self.pe_inst = self._find_option('PUT',  atm_strike)

        for inst in (self.ce_inst, self.pe_inst):
            order = self.place_order(
                instrument_id=inst,
                quantity=1,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                tag="ENTRY",
            )
            self.entry_prices[inst] = self.last_execution[order.id].price.value
            self.leg_hit_sl[inst]   = False

        self.log_info(f"Shorted CE {self.ce_inst} & PE {self.pe_inst}")

        # Subscribe to 3-min bars for the survivors
        self.subscribe_bars(self.ce_inst, duration="3T")
        self.subscribe_bars(self.pe_inst, duration="3T")

    def _check_leg_stop_loss(self):
        """
        Fires the 15 % individual SL.
        """
        for inst, entry_px in self.entry_prices.items():
            if self.leg_hit_sl[inst]:
                continue
            mkt_px = self.market_data.latest_price(inst)
            if mkt_px >= entry_px * (1 + self.leg_sl):
                self.close_position(inst)
                self.leg_hit_sl[inst] = True
                self.log_info(f"Leg {inst} hit 15 % SL")

    def _apply_supertrend(self, bar: PriceBar):
        """
        After one SL is hit, run SuperTrend on the other leg.
        """
        # Determine active leg
        live_legs = [inst for inst, hit in self.leg_hit_sl.items() if not hit]
        if len(live_legs) != 1:
            return          # either both active or none -> do nothing

        inst = live_legs[0]
        self.supertrend.update(bar)

        if not self.supertrend.ready:
            return

        last_st = self.supertrend[-1]
        price   = bar.close

        if price < last_st:
            # Trail SL closer (illustrative: entry + 10 %)
            new_sl = self.entry_prices[inst] * 1.10
            self.amend_stop(inst, new_sl)
        elif price > last_st:
            # Price climbed above ST – exit
            self.close_position(inst)
            self.log_info(f"Exit {inst} – price crossed SuperTrend")

    # ---- utility ------------------------------------------------
    def _find_option(self, right: str, strike: int):
        """
        Returns InstrumentId for the desired option.
        You’d implement this to search your instrument store.
        """
        symbol = f"BANKNIFTY{strike}{right[0]}"
        return self.instrument_store.by_symbol(symbol)

    def amend_stop(self, inst, new_sl):
        """
        Cancels existing stop and places a new one.
        """
        for order in self.orders_by_tag(inst, "LEG_SL"):
            self.cancel_order(order.id)
        self.place_order(
            instrument_id=inst,
            quantity=1,
            side=OrderSide.BUY,
            order_type=OrderType.STOP,
            stop_price=Money(new_sl, "INR"),
            tag="LEG_SL",
        )


# ──────────────────────────────────────────────────────────────────
#  BOILERPLATE TO LAUNCH LOCALLY
# ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    engine = BacktestEngine()
    strategy = BankniftyStraddleSupertrend(
        strategy_id=uuid32(),
        clock=engine.clock,
    )
    engine.add_strategy(strategy)
    engine.run()
