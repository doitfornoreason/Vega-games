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
    ACO_MM_WIDTH = 10           # half-width used when one side is empty
    ACO_MAX_ORDER_SIZE = 10     # cap per order (takes and MM)
    ACO_SKEW_FACTOR = 0.4       # how hard to lean into the mean-reversion trade
    ACO_TAKE_EDGE = 1.5              # base edge required in normal (unactivated) mode
    ACO_ACTIVATION_THRESHOLD = 2     # |FV - anchor| at which skew activates
    ACO_MAX_TAKE_DISTANCE = 5        # cap on edge given up per take (loss cap)
    ACO_FLATTEN_POSITION = 55        # |position| above which we start flattening
    ACO_FLATTEN_TARGET = 50          # flatten back down to this |position|
    ACO_FLATTEN_EDGE = 1             # edge willing to give up when flattening

    # --- Position-scaled edge requirements (NEW) -----------------------------
    # Applied ONLY to the side that would WORSEN our position:
    #   - If position > 0, only the BUY edge is raised (selling stays easy)
    #   - If position < 0, only the SELL edge is raised (buying stays easy)
    # Tier boundaries are absolute position values; edges are floors that
    # combine via max() with whatever normal/activated mode computed.
    ACO_EDGE_POS_TIER1 = 40    # below this, behaves like plain ACO_TAKE_EDGE
    ACO_EDGE_POS_TIER2 = 55    # below this, cautious
    ACO_EDGE_POS_TIER3 = 70    # below this, picky
    ACO_EDGE_TIER1 = 1       # same as ACO_TAKE_EDGE — no change at low pos
    ACO_EDGE_TIER2 = 2.5       # cautious
    ACO_EDGE_TIER3 = 4       # picky
    ACO_EDGE_TIER4 = 4       # near-limit, only take strong mispricings

    def _position_edge(self, position: int) -> float:
        """
        Return the minimum edge required on the side that would worsen inventory.
        Caller applies this asymmetrically based on the sign of position.
        """
        abs_pos = abs(position)
        if abs_pos < self.ACO_EDGE_POS_TIER1:
            return self.ACO_EDGE_TIER1
        if abs_pos < self.ACO_EDGE_POS_TIER2:
            return self.ACO_EDGE_TIER2
        if abs_pos < self.ACO_EDGE_POS_TIER3:
            return self.ACO_EDGE_TIER3
        return self.ACO_EDGE_TIER4

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

               # --- THRESHOLDS ---
                fv_deviation = fair_value - self.ACO_ANCHOR
                abs_deviation = abs(fv_deviation)

                # Base thresholds (same two-mode logic as parent strategy)
                if abs_deviation >= self.ACO_ACTIVATION_THRESHOLD:
                    # Activated: skew shifts thresholds, can cross FV up to MAX_TAKE_DISTANCE
                    buy_threshold = fv_deviation * self.ACO_SKEW_FACTOR
                    sell_threshold = -fv_deviation * self.ACO_SKEW_FACTOR
                    buy_threshold = max(buy_threshold, -self.ACO_MAX_TAKE_DISTANCE)
                    sell_threshold = max(sell_threshold, -self.ACO_MAX_TAKE_DISTANCE)
                else:
                    # Normal: require ACO_TAKE_EDGE of edge on either side
                    buy_threshold = self.ACO_TAKE_EDGE
                    sell_threshold = self.ACO_TAKE_EDGE

                # --- NEW: asymmetric position-scaled edge floor ---
                # Only raise the threshold on the side that would WORSEN the position.
                # This prevents pinning: the farther we drift toward a limit, the
                # higher the edge required to add more in that direction.
                pos_edge_floor = self._position_edge(position)
                if position > 0:
                    buy_threshold = max(buy_threshold, pos_edge_floor)
                elif position < 0:
                    sell_threshold = max(sell_threshold, pos_edge_floor)

                # --- STEP 1: TAKE (skewed / normal / position-scaled) ---
                if asks_exist:
                    for ask_price in sorted(order_depth.sell_orders.keys()):
                        if buy_capacity <= 0 or ask_price >= fair_value - buy_threshold:
                            break
                        take_size = min(self.ACO_MAX_ORDER_SIZE, buy_capacity, -order_depth.sell_orders[ask_price])
                        if take_size > 0:
                            orders.append(Order("ASH_COATED_OSMIUM", ask_price, take_size))
                            buy_capacity -= take_size
                            position += take_size
                if bids_exist:
                    for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
                        if sell_capacity <= 0 or bid_price <= fair_value + sell_threshold:
                            break
                        take_size = min(self.ACO_MAX_ORDER_SIZE, sell_capacity, order_depth.buy_orders[bid_price])
                        if take_size > 0:
                            orders.append(Order("ASH_COATED_OSMIUM", bid_price, -take_size))
                            sell_capacity -= take_size
                            position -= take_size

                # --- STEP 2: FLATTEN if |position| > FLATTEN_POSITION ---
                if position > self.ACO_FLATTEN_POSITION and bids_exist and sell_capacity > 0:
                    flatten_remaining = position - self.ACO_FLATTEN_TARGET
                    for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
                        if sell_capacity <= 0 or flatten_remaining <= 0:
                            break
                        if bid_price <= fair_value - self.ACO_FLATTEN_EDGE:
                            break
                        take_size = min(self.ACO_MAX_ORDER_SIZE, sell_capacity,
                                        flatten_remaining, order_depth.buy_orders[bid_price])
                        if take_size > 0:
                            orders.append(Order("ASH_COATED_OSMIUM", bid_price, -take_size))
                            sell_capacity -= take_size
                            flatten_remaining -= take_size
                            position -= take_size
                elif position < -self.ACO_FLATTEN_POSITION and asks_exist and buy_capacity > 0:
                    flatten_remaining = -self.ACO_FLATTEN_TARGET - position
                    for ask_price in sorted(order_depth.sell_orders.keys()):
                        if buy_capacity <= 0 or flatten_remaining <= 0:
                            break
                        if ask_price >= fair_value + self.ACO_FLATTEN_EDGE:
                            break
                        take_size = min(self.ACO_MAX_ORDER_SIZE, buy_capacity,
                                        flatten_remaining, -order_depth.sell_orders[ask_price])
                        if take_size > 0:
                            orders.append(Order("ASH_COATED_OSMIUM", ask_price, take_size))
                            buy_capacity -= take_size
                            flatten_remaining -= take_size
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