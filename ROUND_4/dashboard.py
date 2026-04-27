import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# --- STREAMLIT CONFIG ---
st.set_page_config(layout="wide")

# --- 1. DATA LOADING ---
@st.cache_data
def load_data():
    prices_list = [pd.read_csv(f"prices_round_4_day_{i}.csv", delimiter=';') for i in range(1, 4)]
    prices = pd.concat(prices_list)

    trades_list = []
    for i in range(1, 4):
        df = pd.read_csv(f"trades_round_4_day_{i}.csv", delimiter=';')
        df["day"] = i
        trades_list.append(df)
    trades = pd.concat(trades_list)

    prices["iteration"] = ((prices["day"] - 1) * 10000 + prices["timestamp"] / 100).astype(int)
    trades["iteration"] = ((trades["day"] - 1) * 10000 + trades["timestamp"] / 100).astype(int)

    participants = sorted(list(pd.concat([trades["buyer"], trades["seller"]]).dropna().unique()))
    return prices, trades, participants

prices, trades, participants = load_data()
symbols = prices["product"].unique()
max_vol_global = trades["quantity"].max() if "quantity" in trades.columns else 100

# --- 2. PNL TIME-SERIES CALCULATION ---
def create_pnl_chart(participant, prices, trades):
    fig = go.Figure()
    combined_pnl = None

    for symbol in symbols:
        sp = prices[prices["product"] == symbol][['iteration', 'bid_price_1', 'ask_price_1']].copy()
        sp['mid'] = (sp['bid_price_1'] + sp['ask_price_1']) / 2
        
        st_trades = trades[(trades["symbol"] == symbol) & 
                           ((trades["buyer"] == participant) | (trades["seller"] == participant))].copy()
        
        if st_trades.empty:
            continue

        st_trades['direction'] = np.where(st_trades['buyer'] == participant, 1, -1)
        st_trades['pos_change'] = st_trades['quantity'] * st_trades['direction']
        st_trades['cash_flow'] = -st_trades['pos_change'] * st_trades['price']

        daily_trades = st_trades.groupby('iteration').agg({'pos_change': 'sum', 'cash_flow': 'sum'}).reset_index()

        df = pd.merge(sp, daily_trades, on='iteration', how='left').fillna(0)
        df['inventory'] = df['pos_change'].cumsum()
        df['cumulative_cash'] = df['cash_flow'].cumsum()
        df['mtm_pnl'] = df['cumulative_cash'] + (df['inventory'] * df['mid'])

        fig.add_trace(go.Scatter(x=df['iteration'], y=df['mtm_pnl'], name=f"{symbol} PnL", mode='lines', opacity=0.6))

        if combined_pnl is None:
            combined_pnl = df[['iteration', 'mtm_pnl']].set_index('iteration')
        else:
            combined_pnl = combined_pnl.add(df[['iteration', 'mtm_pnl']].set_index('iteration'), fill_value=0)

    if combined_pnl is not None:
        fig.add_trace(go.Scatter(x=combined_pnl.index, y=combined_pnl['mtm_pnl'], name="TOTAL PnL", line=dict(color='black', width=3)))

    fig.update_layout(title=f"Cumulative PnL Breakdown: {participant}", xaxis_title="Iteration", yaxis_title="PnL", hovermode="x unified")
    return fig

# --- 3. DASHBOARD PLOTTING (Prices/Trades) ---
def create_dashboard(participant, prices, trades, symbols, max_vol, min_vol, show_crossed, show_limit):
    desired_max_size = 20 
    size_ref = 2. * max_vol / (desired_max_size ** 2)
    fig = make_subplots(rows=len(symbols), cols=1, shared_xaxes=True, subplot_titles=[f"Quotes and Trades: {s}" for s in symbols])

    for i, symbol in enumerate(symbols):
        row = i + 1
        sp = prices[prices["product"] == symbol]
        
        # Plot Quote Lines
        fig.add_trace(go.Scatter(x=sp["iteration"], y=sp["bid_price_1"], mode="lines", line=dict(color="blue", width=1, dash="dash"), opacity=0.4, showlegend=False, hovertemplate="Bid: %{y}<extra></extra>"), row=row, col=1)
        fig.add_trace(go.Scatter(x=sp["iteration"], y=sp["ask_price_1"], mode="lines", line=dict(color="green", width=1), opacity=0.4, showlegend=False, hovertemplate="Ask: %{y}<extra></extra>"), row=row, col=1)

        # Pre-filter trades
        st_trades = trades[(trades["symbol"] == symbol) & (trades["quantity"] >= min_vol)]
        merged = pd.merge(st_trades, sp[['iteration', 'bid_price_1', 'ask_price_1']], on='iteration', how='left')
        
        # Fix the Logic here (merged instead of merged_trades)
        merged['crossed_spread'] = 'No'
        merged.loc[(merged['buyer'] == participant) & (merged['price'] >= merged['ask_price_1']), 'crossed_spread'] = 'Yes'
        merged.loc[(merged['seller'] == participant) & (merged['price'] <= merged['bid_price_1']), 'crossed_spread'] = 'Yes'
        
        for side, color, label in [('buyer', 'Blues', 'Bought'), ('seller', 'Reds', 'Sold')]:
            subset = merged[merged[side] == participant]
            
            # Apply Type Filters
            if not show_crossed and not show_limit:
                subset = subset.iloc[0:0] 
            elif not show_crossed:
                subset = subset[subset['crossed_spread'] == 'No']
            elif not show_limit:
                subset = subset[subset['crossed_spread'] == 'Yes']

            if subset.empty: continue

            syms = ["star" if c == 'Yes' else "circle" for c in subset['crossed_spread']]
            
            fig.add_trace(go.Scatter(
                x=subset["iteration"], y=subset["price"], mode="markers",
                marker=dict(color=subset["quantity"], colorscale=color, size=subset["quantity"], sizemode="area", sizeref=size_ref, sizemin=2, symbol=syms, line=dict(width=0.5, color="grey")),
                customdata=subset[['quantity', 'iteration', 'crossed_spread']], 
                hovertemplate=(
                    "<b>Iteration:</b> %{customdata[1]}<br>"
                    f"<b>{label} Qty:</b> %{{customdata[0]}}<br>"
                    "<b>Crossed Spread:</b> %{customdata[2]}<extra></extra>"
                )), row=row, col=1)
    
    fig.update_layout(height=300 * len(symbols), hovermode="closest", showlegend=False)
    return fig

# --- 4. STREAMLIT UI ---
st.title("Trading Participant Dashboard")

# Metric Calculation for Tables
@st.cache_data
def get_final_metrics(trades, prices, participants):
    results = []
    max_iter = prices['iteration'].max()
    final_mids = prices[prices['iteration'] == max_iter].copy()
    final_mids['mid'] = (final_mids['bid_price_1'] + final_mids['ask_price_1']) / 2
    mid_dict = final_mids.set_index('product')['mid'].to_dict()

    for p in participants:
        for s in symbols:
            t = trades[(trades['symbol'] == s) & ((trades['buyer'] == p) | (trades['seller'] == p))].copy()
            if t.empty: continue
            vol = t['quantity'].sum()
            t['dir'] = np.where(t['buyer'] == p, 1, -1)
            inv = (t['quantity'] * t['dir']).sum()
            cash = (-t['quantity'] * t['dir'] * t['price']).sum()
            final_pnl = cash + (inv * mid_dict.get(s, 0))
            results.append({'participant': p, 'product': s, 'pnl': final_pnl, 'vol': vol})
    return pd.DataFrame(results)

metrics = get_final_metrics(trades, prices, participants)

if not metrics.empty:
    c_pnl, c_vol = st.columns(2)
    with c_pnl:
        st.subheader("Final PnL Summary")
        st.dataframe(metrics.pivot(index='participant', columns='product', values='pnl').fillna(0).style.background_gradient(cmap='RdYlGn', axis=None).format("{:.2f}"), use_container_width=True)
    with c_vol:
        st.subheader("Final Volume Summary")
        st.dataframe(metrics.pivot(index='participant', columns='product', values='vol').fillna(0).style.background_gradient(cmap='Blues', axis=None), use_container_width=True)

st.markdown("---")
st.subheader("Participant Deep Dive")
col_p, col_v, col_f = st.columns([2, 1, 1])

with col_p:
    selected_participant = st.selectbox("Select Participant:", participants)
with col_v:
    min_volume = st.number_input("Min Volume Filter:", min_value=0, value=0)
with col_f:
    st.write("Order Type Filter:")
    show_crossed = st.checkbox("Crossed Spread (Stars)", value=True)
    show_limit = st.checkbox("Limit Orders (Circles)", value=True)

# 5. RENDER CHARTS
st.plotly_chart(create_pnl_chart(selected_participant, prices, trades), use_container_width=True)
st.plotly_chart(create_dashboard(selected_participant, prices, trades, symbols, max_vol_global, min_volume, show_crossed, show_limit), use_container_width=True)