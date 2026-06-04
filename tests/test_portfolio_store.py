"""
Portfolio store tesztek – fájl nélkül, tmp fájlokkal.
"""
import sys, os, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import services.portfolio_store as ps


@pytest.fixture(autouse=True)
def temp_data_file(tmp_path, monkeypatch):
    """Minden teszt saját tmp portfolio fájlt kap."""
    f = tmp_path / "portfolio.json"
    monkeypatch.setattr(ps, "DATA_FILE", str(f))
    return f


def test_load_missing_file_returns_default():
    portfolio = ps.load_portfolio()
    assert isinstance(portfolio, list)
    assert len(portfolio) > 0


def test_load_empty_json_array():
    ps.save_portfolio([])
    result = ps.load_portfolio()
    assert result == []


def test_load_broken_json_returns_default(temp_data_file):
    temp_data_file.write_text("{ broken json !!!}", encoding="utf-8")
    result = ps.load_portfolio()
    assert isinstance(result, list)


def test_load_non_list_json_returns_empty(temp_data_file):
    temp_data_file.write_text('{"ticker": "AAPL"}', encoding="utf-8")
    result = ps.load_portfolio()
    assert result == []


def test_qty_is_float():
    ps.save_portfolio([{"ticker": "AAPL", "name": "Apple", "qty": "12"}])
    result = ps.load_portfolio()
    assert isinstance(result[0]["qty"], float)
    assert result[0]["qty"] == 12.0


def test_missing_fields_filled():
    ps.save_portfolio([{"ticker": "AAPL", "qty": 5}])
    result = ps.load_portfolio()
    item = result[0]
    assert "currency" in item
    assert "exchange" in item
    assert "manually_added" in item
    assert "source" in item


def test_ticker_uppercased():
    ps.save_portfolio([{"ticker": "aapl", "name": "Apple", "qty": 1}])
    result = ps.load_portfolio()
    assert result[0]["ticker"] == "AAPL"


def test_broken_backup_created(temp_data_file):
    temp_data_file.write_text("not json!", encoding="utf-8")
    ps.load_portfolio()
    broken = str(temp_data_file).replace(".json", ".broken.json")
    assert os.path.exists(broken)


def test_save_atomic(tmp_path, monkeypatch):
    f = tmp_path / "portfolio.json"
    monkeypatch.setattr(ps, "DATA_FILE", str(f))
    ps.save_portfolio([{"ticker": "MSFT", "name": "Microsoft", "qty": 3}])
    assert f.exists()
    with open(f, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data[0]["ticker"] == "MSFT"


def test_unicode_preserved(tmp_path, monkeypatch):
    f = tmp_path / "portfolio.json"
    monkeypatch.setattr(ps, "DATA_FILE", str(f))
    ps.save_portfolio([{"ticker": "OTP.BD", "name": "OTP Bánk Nyrt. – éáőű", "qty": 1}])
    with open(f, encoding="utf-8") as fh:
        content = fh.read()
    assert "éáőű" in content
