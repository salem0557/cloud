"""اختبارات وحدة تحليل القاع والارتداد (scanner/rebound.py) على بيانات
اصطناعية حتمية — بلا شبكة."""
import numpy as np
import pandas as pd
import pytest

pytest.importorskip("pandas")

from scanner import config, rebound


def _frame(closes: np.ndarray) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-02", periods=len(closes))
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes * 1.01,
            "Low": closes * 0.99,
            "Close": closes,
            "Volume": np.full(len(closes), 1_000_000.0),
        },
        index=idx,
    )


def _cyclical_dipper(cycles: int = 7) -> np.ndarray:
    """سعر يهبط بحدة من 12$ إلى 8$ ثم يرتد سريعاً إلى 12.5$ ويستقر، بشكل
    متكرر، وينتهي وهو في هبوط جديد قرب القاع — سيناريو الاستراتيجية بالضبط."""
    down = np.linspace(12.5, 8, 10)
    up = np.linspace(8, 12.5, 15)
    # تذبذب خفيف بدل خط مسطح تماماً: خط مسطح يجعل RSI (بطريقة وايلدر)
    # ينهار إلى الصفر من أول شمعة حمراء فيُحتسب "قاع" على القمة.
    flat = 12.5 + 0.15 * np.tile([1.0, -1.0], 30)
    path = np.concatenate([np.concatenate([down, up, flat]) for _ in range(cycles)])
    final_down = np.linspace(12.5, 8.4, 8)
    return np.concatenate([path, final_down])


def test_dipper_with_history_of_rebounds_qualifies():
    stats = rebound.analyze("FAKE", _frame(_cyclical_dipper()))
    assert stats is not None
    assert stats.symbol == "FAKE"
    assert stats.change_20d < 0
    assert stats.episodes >= config.REBOUND_MIN_EPISODES
    assert stats.rebound_rate >= config.REBOUND_MIN_RATE
    assert stats.avg_gain >= config.REBOUND_MIN_GAIN
    assert stats.score > 0


def test_rising_stock_is_rejected():
    closes = np.linspace(10, 30, 600)  # صعود مستمر: ليس في قاع ولا نازلاً
    assert rebound.analyze("UPUP", _frame(closes)) is None


def test_dipper_that_never_rebounds_is_rejected():
    # هبوط طويل بلا أي ارتداد ذي معنى: يمر بشرط الحاضر لكن التاريخ يرفضه
    closes = np.linspace(40, 8, 600)
    assert rebound.analyze("DOWN", _frame(closes)) is None


def test_short_history_is_rejected():
    closes = np.linspace(12, 8, 100)
    assert rebound.analyze("NEW", _frame(closes)) is None
