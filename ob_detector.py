import numpy as np
import pandas as pd


def detect_order_blocks(df, lookback=5, displacement_factor=1.5):
    """Detects Bullish and Bearish Order Blocks based on Market Structure Break

    and strong candle displacement.
    """
    df = df.copy()

    # Calculate Average True Range (ATR) or average body size for displacement filter
    candle_body = (df["Close"] - df["Open"]).abs()
    avg_body = candle_body.rolling(window=20).mean()

    # Initialize columns
    df["Bullish_OB_High"] = np.nan
    df["Bullish_OB_Low"] = np.nan
    df["Bearish_OB_High"] = np.nan
    df["Bearish_OB_Low"] = np.nan

    # Loop through the DataFrame (starting after lookback windows)
    for i in range(lookback, len(df)):
        current_close = df["Close"].iloc[i]
        current_open = df["Open"].iloc[i]

        # 1. DETECT BULLISH ORDER BLOCK
        # Look for a strong bullish impulse (displacement)
        is_bullish_impulse = (current_close > current_open) and (
            candle_body.iloc[i] > avg_body.iloc[i] * displacement_factor
        )

        if is_bullish_impulse:
            # Check if this impulse breaks a recent structure high (BOS)
            recent_high = df["High"].iloc[i - lookback : i].max()
            if current_close > recent_high:
                # Walk back to find the last bearish candle before the move
                for j in range(i - 1, i - lookback - 1, -1):
                    if df["Close"].iloc[j] < df["Open"].iloc[j]:
                        # Map the Order Block Zone
                        df.at[i, "Bullish_OB_High"] = df["High"].iloc[j]
                        df.at[i, "Bullish_OB_Low"] = df["Low"].iloc[j]
                        break

        # 2. DETECT BEARISH ORDER BLOCK
        # Look for a strong bearish impulse (displacement)
        is_bearish_impulse = (current_close < current_open) and (
            candle_body.iloc[i] > avg_body.iloc[i] * displacement_factor
        )

        if is_bearish_impulse:
            # Check if this impulse breaks a recent structure low (BOS)
            recent_low = df["Low"].iloc[i - lookback : i].min()
            if current_close < recent_low:
                # Walk back to find the last bullish candle before the move
                for j in range(i - 1, i - lookback - 1, -1):
                    if df["Close"].iloc[j] > df["Open"].iloc[j]:
                        # Map the Order Block Zone
                        df.at[i, "Bearish_OB_High"] = df["High"].iloc[j]
                        df.at[i, "Bearish_OB_Low"] = df["Low"].iloc[j]
                        break

    return df


# --- Quick Example Usage ---
# Generate dummy OHLCV data
np.random.seed(42)
data = {
    "Open": [100 + np.random.randn() for _ in range(30)],
    "High": [102 + np.random.randn() for _ in range(30)],
    "Low": [98 + np.random.randn() for _ in range(30)],
    "Close": [101 + np.random.randn() for _ in range(30)],
}
# Induce a manual bullish break out scenario for testing
data["Close"][10] = 115.0
data["High"][10] = 116.0

df_ohlc = pd.DataFrame(data)
df_with_ob = detect_order_blocks(df_ohlc, lookback=5)

# Filter out rows where an Order Block was detected
detected_blocks = df_with_ob.dropna(
    subset=[
        "Bullish_OB_High",
        "Bearish_OB_High",
    ],
    how="all",
)
print(
    detected_blocks[
        ["Open", "Close", "Bullish_OB_High", "Bullish_OB_Low"]
    ].head()
)

