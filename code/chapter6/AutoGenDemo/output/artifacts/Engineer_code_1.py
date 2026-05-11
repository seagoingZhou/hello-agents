import streamlit as st
import requests
import datetime
import time

# ---------- 数据获取模块 ----------
def get_bitcoin_price():
    """从 CoinGecko API 获取比特币当前价格、24小时涨跌额和涨跌幅"""
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "ids": "bitcoin",
        "order": "market_cap_desc",
        "per_page": 1,
        "page": 1,
        "sparkline": "false",
        "price_change_percentage": "24h"
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if not data:
            raise ValueError("API 返回空数据")
        coin = data[0]
        price = coin["current_price"]
        change_24h = coin["price_change_24h"]          # 涨跌额
        change_pct_24h = coin["price_change_percentage_24h"]  # 涨跌幅（%）
        return price, change_24h, change_pct_24h
    except requests.exceptions.RequestException as e:
        raise ConnectionError(f"网络请求失败: {e}")
    except (KeyError, IndexError, ValueError) as e:
        raise ValueError(f"数据解析错误: {e}")

# ---------- 主应用 ----------
def main():
    st.set_page_config(
        page_title="比特币实时价格",
        page_icon="₿",
        layout="centered"
    )

    # 初始化 session_state，用于缓存数据和错误状态
    if "price" not in st.session_state:
        st.session_state.price = None
        st.session_state.change_24h = None
        st.session_state.change_pct_24h = None
        st.session_state.error = None
        st.session_state.last_fetch_time = None

    # 页面标题
    st.title("₿ 比特币实时价格")

    # 刷新按钮区域
    col_refresh, col_info = st.columns([1, 3])
    with col_refresh:
        refresh_clicked = st.button("🔄 刷新", type="primary")
    with col_info:
        if st.session_state.last_fetch_time:
            st.caption(f"上次更新: {st.session_state.last_fetch_time.strftime('%H:%M:%S')}")
        else:
            st.caption("点击刷新获取数据")

    # 触发数据获取：首次加载或点击刷新
    if refresh_clicked or st.session_state.price is None:
        with st.spinner("正在获取最新价格..."):
            try:
                price, change_24h, change_pct_24h = get_bitcoin_price()
                # 更新 session_state
                st.session_state.price = price
                st.session_state.change_24h = change_24h
                st.session_state.change_pct_24h = change_pct_24h
                st.session_state.error = None
                st.session_state.last_fetch_time = datetime.datetime.now()
            except (ConnectionError, ValueError) as e:
                st.session_state.error = str(e)
                # 保留上次有效数据，不清除

    # 错误处理（显示错误信息，不覆盖已有数据）
    if st.session_state.error:
        st.error(f"数据获取失败：{st.session_state.error}\n请检查网络连接后重试。")

    # 数据展示（仅当有价格数据时）
    if st.session_state.price is not None:
        price = st.session_state.price
        change_24h = st.session_state.change_24h
        change_pct_24h = st.session_state.change_pct_24h

        # 根据涨跌确定颜色和箭头
        if change_24h >= 0:
            color = "green"
            arrow = "▲"
        else:
            color = "red"
            arrow = "▼"

        # 三列布局：价格、涨跌额、涨跌幅
        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown("#### 当前价格")
            st.markdown(
                f"<h1 style='font-size:48px;'>${price:,.2f}</h1>",
                unsafe_allow_html=True
            )
        with col2:
            st.markdown("#### 24h 涨跌额")
            st.markdown(
                f"<h2 style='color:{color}; font-size:32px;'>{arrow} {change_24h:+,.2f} USD</h2>",
                unsafe_allow_html=True
            )
        with col3:
            st.markdown("#### 24h 涨跌幅")
            pct_str = f"{change_pct_24h:+.2f}%"
            st.markdown(
                f"<h2 style='color:{color}; font-size:32px;'>{arrow} {pct_str}</h2>",
                unsafe_allow_html=True
            )

        # 更新时间戳
        if st.session_state.last_fetch_time:
            st.caption(
                f"数据更新于: {st.session_state.last_fetch_time.strftime('%Y-%m-%d %H:%M:%S')}"
            )

        # 键盘快捷键提示（仅文本说明，实际按键由浏览器默认处理）
        st.markdown("---")
        st.caption("提示: 点击上方【刷新】按钮或按键盘 `R` 键刷新数据")

    else:
        # 首次加载且还没有数据（出错时也显示该提示）
        if not st.session_state.error:
            st.info("等待首次数据获取...")

if __name__ == "__main__":
    main()