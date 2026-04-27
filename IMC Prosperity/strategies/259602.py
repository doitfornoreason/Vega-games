from datamodel import OrderDepth, TradingState, Order
from typing import List
import json


class Trader:
    POSITION_LIMIT = 80
    IPR_TARGET_POSITION = 75
    IPR_ORDER_SIZE = 5
    IPR_MIN_POSITION = 72       # never drop below this
    IPR_STRONG_EV_THRESHOLD = 4
    IPR_SKEW_FACTOR = 0.4        # per unit below target, reduces EV threshold required to buy
    ACO_ANCHOR = 10000
    ACO_MM_WIDTH = 10
    ACO_MAX_ORDER_SIZE = 20
    ACO_TRANSITION_START = 40        # |pos| at which ref starts blending anchor → FV
    ACO_TRANSITION_END = 70          # |pos| at which ref is fully on FV
    ACO_STRONG_EV = 2                # edge required beyond |pos| = 70 to keep extending
    ACO_SKEW_FACTOR = 0.4            # per unit past 70, adds to extend threshold, subtracts from reduce threshold
    ACO_MAX_TAKE_DISTANCE = 4        # cap on edge given up per take
    ACO_FLATTEN_POSITION = 70        # flatten step threshold (unchanged)
    ACO_FLATTEN_TARGET = 65
    ACO_FLATTEN_POSITION = 70        # |pos| above which flatten kicks in
    ACO_FLATTEN_SLOPE = 0.2          # edge given up per unit past 70 (0 at 70, 2 at 80)
    # ACO_FLATTEN_TARGET removed

    def compute_fair_value(self, order_depth: OrderDepth, prev_makers: dict):
        sorted_bids = sorted(order_depth.buy_orders.items(), key=lambda x: -x[0])
        sorted_asks = sorted(order_depth.sell_orders.items(), key=lambda x: x[0])

        def level(lst, i, sign=1):
            if len(lst) > i:
                return lst[i][0], sign * lst[i][1]
            return None, None

        bid_price_1, bid_volume_1 = level(sorted_bids, 0)
        bid_price_2, bid_volume_2 = level(sorted_bids, 1)
        bid_price_3, bid_volume_3 = level(sorted_bids, 2)
        ask_price_1, ask_volume_1 = level(sorted_asks, 0, sign=-1)
        ask_price_2, ask_volume_2 = level(sorted_asks, 1, sign=-1)
        ask_price_3, ask_volume_3 = level(sorted_asks, 2, sign=-1)

        # --- Fresh maker values computed from the current book ---
        f_mbp1 = f_mbv1 = f_mbp2 = f_mbv2 = None
        f_map1 = f_mav1 = f_map2 = f_mav2 = None

        # BID SIDE
        if bid_price_3 is not None:
            f_mbp1, f_mbv1 = bid_price_2, bid_volume_2
            f_mbp2, f_mbv2 = bid_price_3, bid_volume_3
        elif bid_price_2 is not None:
            if bid_volume_1 < 10 or abs(bid_price_1 - bid_price_2) >= 5:
                if bid_volume_2 >= 20:
                    f_mbp2, f_mbv2 = bid_price_2, bid_volume_2
                else:
                    f_mbp1, f_mbv1 = bid_price_2, bid_volume_2
            else:
                f_mbp1, f_mbv1 = bid_price_1, bid_volume_1
                f_mbp2, f_mbv2 = bid_price_2, bid_volume_2
        elif bid_price_1 is not None:
            if bid_volume_1 >= 20:
                f_mbp2, f_mbv2 = bid_price_1, bid_volume_1
            elif bid_volume_1 >= 10:
                f_mbp1, f_mbv1 = bid_price_1, bid_volume_1

        # ASK SIDE
        if ask_price_3 is not None:
            f_map1, f_mav1 = ask_price_2, ask_volume_2
            f_map2, f_mav2 = ask_price_3, ask_volume_3
        elif ask_price_2 is not None:
            if ask_volume_1 < 10 or abs(ask_price_1 - ask_price_2) >= 5:
                if ask_volume_2 >= 20:
                    f_map2, f_mav2 = ask_price_2, ask_volume_2
                else:
                    f_map1, f_mav1 = ask_price_2, ask_volume_2
            else:
                f_map1, f_mav1 = ask_price_1, ask_volume_1
                f_map2, f_mav2 = ask_price_2, ask_volume_2
        elif ask_price_1 is not None:
            if ask_volume_1 >= 20:
                f_map2, f_mav2 = ask_price_1, ask_volume_1
            elif ask_volume_1 >= 10:
                f_map1, f_mav1 = ask_price_1, ask_volume_1

       # --- UPDATE MEMORY: store most recent fresh value per level ---
        new_prev = dict(prev_makers)
        if f_mbp1 is not None:
            new_prev["maker_bid_price_1"] = f_mbp1
            new_prev["maker_bid_volume_1"] = f_mbv1
        if f_mbp2 is not None:
            new_prev["maker_bid_price_2"] = f_mbp2
            new_prev["maker_bid_volume_2"] = f_mbv2
        if f_map1 is not None:
            new_prev["maker_ask_price_1"] = f_map1
            new_prev["maker_ask_volume_1"] = f_mav1
        if f_map2 is not None:
            new_prev["maker_ask_price_2"] = f_map2
            new_prev["maker_ask_volume_2"] = f_mav2

        # --- Use most recent price per level (fresh or remembered) ---
        mbp1 = new_prev.get("maker_bid_price_1")
        mbp2 = new_prev.get("maker_bid_price_2")
        map1 = new_prev.get("maker_ask_price_1")
        map2 = new_prev.get("maker_ask_price_2")

        # --- FAIR VALUE ---
        if (mbp1 is not None and mbp2 is not None
                and map1 is not None and map2 is not None):
            fair = (mbp1 + mbp2 + map1 + map2) / 4
        elif mbp1 is not None and map1 is not None:
            fair = (mbp1 + map1) / 2
        elif mbp2 is not None and map2 is not None:
            fair = (mbp2 + map2) / 2
        else:
            fair = None

        return fair, new_prev

    def run(self, state: TradingState):
        result = {}

        # --- RESTORE memory ---
        if state.traderData:
            memory = json.loads(state.traderData)
        else:
            memory = {}
        memory.setdefault("aco_prev_makers", {})

        if "INTARIAN_PEPPER_ROOT" in state.order_depths:
            order_depth: OrderDepth = state.order_depths["INTARIAN_PEPPER_ROOT"]
            orders: List[Order] = []

            # --- initial_mid init ---
            if "initial_mid" in memory:
                initial_mid = memory["initial_mid"]
            else:
                best_bid = max(order_depth.buy_orders.keys()) if order_depth.buy_orders else None
                best_ask = min(order_depth.sell_orders.keys()) if order_depth.sell_orders else None
                if best_bid is not None and best_ask is not None:
                    initial_mid = round((best_bid + best_ask) / 2 / 1000) * 1000
                elif best_bid is not None:
                    initial_mid = round(best_bid / 1000) * 1000
                elif best_ask is not None:
                    initial_mid = round(best_ask / 1000) * 1000
                else:
                    initial_mid = 10000
                memory["initial_mid"] = initial_mid

            # --- FAIR VALUE ---
            fair_value = initial_mid + state.timestamp / 1000

            # --- POSITION & CAPACITY ---
            position = state.position.get("INTARIAN_PEPPER_ROOT", 0)
            pos_deficit = self.IPR_TARGET_POSITION - position
            buy_capacity = self.POSITION_LIMIT - position
            sell_capacity = min(self.POSITION_LIMIT + position, position - self.IPR_MIN_POSITION)
            sell_capacity = max(sell_capacity, 0)

            bids_exist = len(order_depth.buy_orders) > 0
            asks_exist = len(order_depth.sell_orders) > 0
            best_bid = max(order_depth.buy_orders.keys()) if bids_exist else None
            best_ask = min(order_depth.sell_orders.keys()) if asks_exist else None

            # --- SKEW: reduce EV threshold required to buy when below target ---
            units_below_target = max(self.IPR_TARGET_POSITION - position, 0)
            skewed_buy_threshold = max(0, self.IPR_STRONG_EV_THRESHOLD - units_below_target * self.IPR_SKEW_FACTOR)
            skewed_sell_threshold = self.IPR_STRONG_EV_THRESHOLD

            # --- STEP 0: First 10 timesteps — aggressively buy to reach target position ---
            if state.timestamp < 1000 and asks_exist:
                ask_ceiling = fair_value + 7
                for ask_price in sorted(order_depth.sell_orders.keys()):
                    if ask_price <= ask_ceiling and buy_capacity > 0 and pos_deficit > 0:
                        take_size = min(pos_deficit, buy_capacity, -order_depth.sell_orders[ask_price])
                        if take_size > 0:
                            orders.append(Order("INTARIAN_PEPPER_ROOT", ask_price, take_size))
                            buy_capacity -= take_size
                            pos_deficit -= take_size

            # --- STEP 1: TAKE strongly positive EV regardless of target position ---
            if asks_exist:
                for ask_price in sorted(order_depth.sell_orders.keys()):
                    if ask_price < fair_value - skewed_buy_threshold and buy_capacity > 0:
                        take_size = min(self.IPR_ORDER_SIZE, buy_capacity, -order_depth.sell_orders[ask_price])
                        if take_size > 0:
                            orders.append(Order("INTARIAN_PEPPER_ROOT", ask_price, take_size))
                            buy_capacity -= take_size
                            pos_deficit -= take_size
            if bids_exist:
                for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
                    if bid_price > fair_value + skewed_sell_threshold and sell_capacity > 0:
                        take_size = min(self.IPR_ORDER_SIZE, sell_capacity, order_depth.buy_orders[bid_price])
                        if take_size > 0:
                            orders.append(Order("INTARIAN_PEPPER_ROOT", bid_price, -take_size))
                            sell_capacity -= take_size
                            pos_deficit += take_size

            # --- STEP 2: TAKE normal positive EV orders to move toward target ---
            if pos_deficit > 0 and asks_exist:
                for ask_price in sorted(order_depth.sell_orders.keys()):
                    if ask_price < fair_value and buy_capacity > 0:
                        take_size = min(pos_deficit, self.IPR_ORDER_SIZE, buy_capacity, -order_depth.sell_orders[ask_price])
                        if take_size > 0:
                            orders.append(Order("INTARIAN_PEPPER_ROOT", ask_price, take_size))
                            buy_capacity -= take_size
                            pos_deficit -= take_size
            elif pos_deficit < 0 and bids_exist:
                for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
                    if bid_price > fair_value and sell_capacity > 0:
                        take_size = min(-pos_deficit, self.IPR_ORDER_SIZE, sell_capacity, order_depth.buy_orders[bid_price])
                        if take_size > 0:
                            orders.append(Order("INTARIAN_PEPPER_ROOT", bid_price, -take_size))
                            sell_capacity -= take_size
                            pos_deficit += take_size

            # --- STEP 3: MARKET MAKE with remaining capacity ---
            mm_buy_capacity = min(buy_capacity, self.POSITION_LIMIT - self.IPR_TARGET_POSITION + (self.IPR_TARGET_POSITION - position - pos_deficit))
            mm_sell_capacity = sell_capacity
            if bids_exist and asks_exist:
                our_bid = best_bid + 1
                our_ask = best_ask - 1
            elif asks_exist and not bids_exist:
                our_bid = int(fair_value - 6)
                our_ask = best_ask - 1
            elif bids_exist and not asks_exist:
                our_bid = best_bid + 1
                our_ask = -int(-(fair_value + 6) // 1)
            else:
                our_bid = int(fair_value - 6)
                our_ask = -int(-(fair_value + 6) // 1)
            if mm_buy_capacity > 0 and our_bid < fair_value - skewed_buy_threshold:
                orders.append(Order("INTARIAN_PEPPER_ROOT", our_bid, min(self.IPR_ORDER_SIZE, mm_buy_capacity)))
            if mm_sell_capacity > 0 and our_ask > fair_value + skewed_sell_threshold:
                orders.append(Order("INTARIAN_PEPPER_ROOT", our_ask, -min(self.IPR_ORDER_SIZE, mm_sell_capacity)))

            result["INTARIAN_PEPPER_ROOT"] = orders

        if "ASH_COATED_OSMIUM" in state.order_depths:
            order_depth: OrderDepth = state.order_depths["ASH_COATED_OSMIUM"]
            orders: List[Order] = []

            # --- FAIR VALUE ---
            fair_value, memory["aco_prev_makers"] = self.compute_fair_value(
                order_depth, memory["aco_prev_makers"]
            )

            if fair_value is not None:
                # --- POSITION & CAPACITY ---
                position = state.position.get("ASH_COATED_OSMIUM", 0)
                buy_capacity = self.POSITION_LIMIT - position
                sell_capacity = self.POSITION_LIMIT + position

                bids_exist = len(order_depth.buy_orders) > 0
                asks_exist = len(order_depth.sell_orders) > 0
                best_bid = max(order_depth.buy_orders.keys()) if bids_exist else None
                best_ask = min(order_depth.sell_orders.keys()) if asks_exist else None

              # --- REFERENCE & THRESHOLDS ---
                abs_pos = abs(position)

                if abs_pos >= self.ACO_TRANSITION_END:
                    # Loaded: reference is FV. Extending position scales with skew (no constant),
                    # reducing position requires no edge (take at FV).
                    ref_price = fair_value
                    units_past = abs_pos - self.ACO_TRANSITION_END  # 0..10
                    if position > 0:
                        buy_threshold = units_past * self.ACO_SKEW_FACTOR   # extend long → hard
                        sell_threshold = 0                                   # reduce long → just FV
                    else:
                        buy_threshold = 0                                    # reduce short → just FV
                        sell_threshold = units_past * self.ACO_SKEW_FACTOR  # extend short → hard
                    ask_ceiling = min(ref_price - buy_threshold,
                                      fair_value + self.ACO_MAX_TAKE_DISTANCE)
                    bid_floor = max(ref_price + sell_threshold,
                                    fair_value - self.ACO_MAX_TAKE_DISTANCE)
                else:
                    # Not loaded: reference blends from anchor (at pos ≤ 50) to FV (at pos = 70)
                    if abs_pos <= self.ACO_TRANSITION_START:
                        ref_price = self.ACO_ANCHOR
                    else:
                        t = (abs_pos - self.ACO_TRANSITION_START) / (
                            self.ACO_TRANSITION_END - self.ACO_TRANSITION_START
                        )
                        ref_price = self.ACO_ANCHOR * (1 - t) + fair_value * t
                    # No edge requirement in this regime, just cap vs FV
                    ask_ceiling = min(ref_price, fair_value + self.ACO_MAX_TAKE_DISTANCE)
                    bid_floor = max(ref_price, fair_value - self.ACO_MAX_TAKE_DISTANCE)

                # --- STEP 1: TAKE ---
                if asks_exist:
                    for ask_price in sorted(order_depth.sell_orders.keys()):
                        if buy_capacity <= 0 or ask_price >= ask_ceiling:
                            break
                        take_size = min(self.ACO_MAX_ORDER_SIZE, buy_capacity,
                                        -order_depth.sell_orders[ask_price])
                        if take_size > 0:
                            orders.append(Order("ASH_COATED_OSMIUM", ask_price, take_size))
                            buy_capacity -= take_size
                            position += take_size
                if bids_exist:
                    for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
                        if sell_capacity <= 0 or bid_price <= bid_floor:
                            break
                        take_size = min(self.ACO_MAX_ORDER_SIZE, sell_capacity,
                                        order_depth.buy_orders[bid_price])
                        if take_size > 0:
                            orders.append(Order("ASH_COATED_OSMIUM", bid_price, -take_size))
                            sell_capacity -= take_size
                            position -= take_size

                # --- STEP 2: FLATTEN (negative-EV only, cap at FLATTEN_POSITION) ---
                # Positive EV is already handled by STEP 1, so we skip those bids/asks here.
                if position > self.ACO_FLATTEN_POSITION and bids_exist and sell_capacity > 0:
                    units_past = position - self.ACO_FLATTEN_POSITION
                    flatten_edge = units_past * self.ACO_FLATTEN_SLOPE
                    for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
                        if sell_capacity <= 0:
                            break
                        if bid_price >= fair_value:
                            continue                              # +EV handled by STEP 1
                        if bid_price <= fair_value - flatten_edge:
                            break                                 # too deep, give up
                        cap = position - self.ACO_FLATTEN_POSITION
                        if cap <= 0:
                            break                                 # back at 70, stop
                        take_size = min(self.ACO_MAX_ORDER_SIZE, sell_capacity, cap,
                                        order_depth.buy_orders[bid_price])
                        if take_size > 0:
                            orders.append(Order("ASH_COATED_OSMIUM", bid_price, -take_size))
                            sell_capacity -= take_size
                            position -= take_size
                elif position < -self.ACO_FLATTEN_POSITION and asks_exist and buy_capacity > 0:
                    units_past = -position - self.ACO_FLATTEN_POSITION
                    flatten_edge = units_past * self.ACO_FLATTEN_SLOPE
                    for ask_price in sorted(order_depth.sell_orders.keys()):
                        if buy_capacity <= 0:
                            break
                        if ask_price <= fair_value:
                            continue                              # +EV handled by STEP 1
                        if ask_price >= fair_value + flatten_edge:
                            break
                        cap = -position - self.ACO_FLATTEN_POSITION
                        if cap <= 0:
                            break
                        take_size = min(self.ACO_MAX_ORDER_SIZE, buy_capacity, cap,
                                        -order_depth.sell_orders[ask_price])
                        if take_size > 0:
                            orders.append(Order("ASH_COATED_OSMIUM", ask_price, take_size))
                            buy_capacity -= take_size
                            position += take_size
                            
                # --- STEP 3: MARKET MAKE by improving best bid/ask by 1 ---
                # If improving the inside would be negative EV, try improving the
                # 2nd-best level instead. If there is no 2nd-best on that side,
                # fall back to FV ± ACO_MM_WIDTH.
                sorted_bid_prices = sorted(order_depth.buy_orders.keys(), reverse=True)
                sorted_ask_prices = sorted(order_depth.sell_orders.keys())

                if sorted_bid_prices:
                    our_bid = sorted_bid_prices[0] + 1
                    if our_bid >= fair_value:
                        if len(sorted_bid_prices) >= 2:
                            our_bid = sorted_bid_prices[1] + 1
                        else:
                            our_bid = int((fair_value - self.ACO_MM_WIDTH) // 1)
                else:
                    our_bid = int((fair_value - self.ACO_MM_WIDTH) // 1)

                if sorted_ask_prices:
                    our_ask = sorted_ask_prices[0] - 1
                    if our_ask <= fair_value:
                        if len(sorted_ask_prices) >= 2:
                            our_ask = sorted_ask_prices[1] - 1
                        else:
                            our_ask = -int(-(fair_value + self.ACO_MM_WIDTH) // 1)
                else:
                    our_ask = -int(-(fair_value + self.ACO_MM_WIDTH) // 1)

                if buy_capacity > 0 and our_bid < fair_value:
                    orders.append(Order("ASH_COATED_OSMIUM", our_bid,
                                        min(self.ACO_MAX_ORDER_SIZE, buy_capacity)))
                if sell_capacity > 0 and our_ask > fair_value:
                    orders.append(Order("ASH_COATED_OSMIUM", our_ask,
                                        -min(self.ACO_MAX_ORDER_SIZE, sell_capacity)))

            result["ASH_COATED_OSMIUM"] = orders

        traderData = json.dumps(memory)
        conversions = 0
        return result, conversions, traderData