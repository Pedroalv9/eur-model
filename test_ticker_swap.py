"""
Compara o modelo EUR usando o spread Franca-Alemanha 10y antigo (.FRNGE10 U Index)
vs o novo (.FFRGE10Y U Index), para avaliar se a troca compromete o modelo.

Nao altera eur_model.py nem o HTML. Apenas roda as duas regressoes lado a lado
e imprime um comparativo.
"""

import blpapi
import pandas as pd
import statsmodels.api as sm

START_DATE = "2021-04-01"
BBG_FIELD = "PX_LAST"

TICKERS = [
    "EUR Curncy",
    ".GEUSREAL U Index",
    ".GEVSPER U Index",
    ".EUXUS2Y U Index",
    ".FRNGE10 U Index",
    ".FFRGE10Y U Index",
]

RENAME = {
    "EUR Curncy": "EURSpot",
    ".GEUSREAL U Index": "RealSpread",
    ".GEVSPER U Index": "GEvsPer",
    ".EUXUS2Y U Index": "STSpread",
    ".FRNGE10 U Index": "FRxGESpread_old",
    ".FFRGE10Y U Index": "FRxGESpread_new",
}


def _load_with_blpapi(start_date, end_date):
    session_options = blpapi.SessionOptions()
    session_options.setServerHost("localhost")
    session_options.setServerPort(8194)

    session = blpapi.Session(session_options)
    if not session.start():
        raise RuntimeError("Failed to start Bloomberg session.")

    if not session.openService("//blp/refdata"):
        session.stop()
        raise RuntimeError("Failed to open Bloomberg //blp/refdata service.")

    try:
        service = session.getService("//blp/refdata")
        request = service.createRequest("HistoricalDataRequest")

        for ticker in TICKERS:
            request.getElement("securities").appendValue(ticker)
        request.getElement("fields").appendValue(BBG_FIELD)

        request.set("startDate", pd.Timestamp(start_date).strftime("%Y%m%d"))
        request.set("endDate", pd.Timestamp(end_date).strftime("%Y%m%d"))
        request.set("periodicitySelection", "DAILY")
        request.set("nonTradingDayFillOption", "NON_TRADING_WEEKDAYS")
        request.set("nonTradingDayFillMethod", "PREVIOUS_VALUE")

        session.sendRequest(request)

        records = []
        security_errors = []

        while True:
            event = session.nextEvent(5000)
            for msg in event:
                if msg.hasElement("responseError"):
                    raise RuntimeError(f"Bloomberg responseError: {msg.getElement('responseError')}")

                if not msg.hasElement("securityData"):
                    continue

                security_data = msg.getElement("securityData")
                security_name = security_data.getElementAsString("security")

                if security_data.hasElement("securityError"):
                    security_errors.append(f"{security_name}: {security_data.getElement('securityError')}")
                    continue

                field_data = security_data.getElement("fieldData")
                for i in range(field_data.numValues()):
                    row = field_data.getValueAsElement(i)
                    if not row.hasElement(BBG_FIELD):
                        continue
                    value_elem = row.getElement(BBG_FIELD)
                    if value_elem.isNull():
                        continue
                    records.append(
                        {
                            "date_column": pd.Timestamp(row.getElementAsDatetime("date")),
                            "ticker": security_name,
                            "value": float(row.getElementAsFloat(BBG_FIELD)),
                        }
                    )

            if event.eventType() == blpapi.Event.RESPONSE:
                break

        if security_errors:
            raise RuntimeError("Bloomberg securityError(s): " + " | ".join(security_errors))

        if not records:
            raise RuntimeError("No fieldData returned from Bloomberg.")

        data = (
            pd.DataFrame(records)
            .pivot_table(index="date_column", columns="ticker", values="value", aggfunc="last")
            .reset_index()
        )
        return data
    finally:
        session.stop()


def load_data():
    end_date = pd.Timestamp.today().strftime("%Y-%m-%d")
    data = _load_with_blpapi(START_DATE, end_date)

    missing = [c for c in RENAME if c not in data.columns]
    if missing:
        raise KeyError(f"Bloomberg data is missing expected tickers: {missing}")

    data = data.rename(columns=RENAME)
    data["date_column"] = pd.to_datetime(data["date_column"], errors="coerce")
    data = data.dropna(subset=["date_column"]).sort_values("date_column")

    bday_index = pd.bdate_range(start=data["date_column"].min(), end=pd.Timestamp.today().normalize())
    data = (
        data.set_index("date_column")
        .reindex(bday_index)
        .ffill()
        .reset_index()
        .rename(columns={"index": "date_column"})
    )
    return data


def run_ols(data, fr_col, label):
    cols = ["EURSpot", "RealSpread", "GEvsPer", "STSpread", fr_col]
    df = data.dropna(subset=cols)

    y = df["EURSpot"]
    X = df[["RealSpread", "GEvsPer", "STSpread", fr_col]]
    X = sm.add_constant(X)

    model = sm.OLS(y, X, missing="drop")
    results = model.fit()

    print(f"\n{'=' * 70}")
    print(f"Modelo: {label}  (coluna FR: {fr_col}, n={len(df)})")
    print("=" * 70)
    print(results.summary2().tables[1])
    print(f"R-squared: {results.rsquared:.5f}  |  Adj R-squared: {results.rsquared_adj:.5f}")
    print(f"Resid std dev: {results.resid.std():.5f}")

    return results, df


def main():
    print("Baixando dados da Bloomberg...")
    data = load_data()
    print(f"Periodo: {data['date_column'].min().date()} a {data['date_column'].max().date()}")
    print(f"Total de linhas: {len(data)}")

    # Diagnostico da quebra de serie
    old = data["FRxGESpread_old"]
    new = data["FRxGESpread_new"]
    diff = (old - new).dropna()
    print("\n--- Diagnostico das series FR-GE 10y (nivel old - new) ---")
    print(f"Diferenca media (old - new): {diff.mean():.5f}")
    print(f"Diferenca std  (old - new): {diff.std():.5f}")
    print(f"Diferenca max abs (old - new): {diff.abs().max():.5f}")
    biggest = diff.abs().sort_values(ascending=False).head(5)
    print("Maiores diferencas (old vs new):")
    for idx in biggest.index:
        d = data.loc[idx, "date_column"]
        print(f"  {d.date()}: old={old.loc[idx]:.4f}  new={new.loc[idx]:.4f}  diff={diff.loc[idx]:.4f}")

    # Diagnostico: maiores saltos diarios (1a diferenca) em cada serie -> identifica o "spike"
    d_old = old.diff().abs()
    d_new = new.diff().abs()
    print("\n--- Maiores saltos diarios (1a diferenca absoluta) - serie ANTIGA ---")
    top_old = d_old.sort_values(ascending=False).head(5)
    for idx in top_old.index:
        d = data.loc[idx, "date_column"]
        print(f"  {d.date()}: old_t-1={old.loc[idx-1]:.4f} -> old_t={old.loc[idx]:.4f}  (delta={old.diff().loc[idx]:.4f})  | new_t-1={new.loc[idx-1]:.4f} -> new_t={new.loc[idx]:.4f}  (delta={new.diff().loc[idx]:.4f})")

    print("\n--- Maiores saltos diarios (1a diferenca absoluta) - serie NOVA ---")
    top_new = d_new.sort_values(ascending=False).head(5)
    for idx in top_new.index:
        d = data.loc[idx, "date_column"]
        print(f"  {d.date()}: old_t-1={old.loc[idx-1]:.4f} -> old_t={old.loc[idx]:.4f}  (delta={old.diff().loc[idx]:.4f})  | new_t-1={new.loc[idx-1]:.4f} -> new_t={new.loc[idx]:.4f}  (delta={new.diff().loc[idx]:.4f})")

    res_old, df_old = run_ols(data, "FRxGESpread_old", "ANTIGO (.FRNGE10 U Index)")
    res_new, df_new = run_ols(data, "FRxGESpread_new", "NOVO (.FFRGE10Y U Index)")

    print(f"\n{'=' * 70}")
    print("COMPARATIVO RESUMIDO")
    print("=" * 70)
    print(f"{'Metrica':<25}{'Antigo':>15}{'Novo':>15}")
    print(f"{'N obs':<25}{len(df_old):>15}{len(df_new):>15}")
    print(f"{'R-squared':<25}{res_old.rsquared:>15.5f}{res_new.rsquared:>15.5f}")
    print(f"{'Adj R-squared':<25}{res_old.rsquared_adj:>15.5f}{res_new.rsquared_adj:>15.5f}")
    print(f"{'Resid std':<25}{res_old.resid.std():>15.5f}{res_new.resid.std():>15.5f}")
    beta_old = res_old.params.get("FRxGESpread_old", float("nan"))
    beta_new = res_new.params.get("FRxGESpread_new", float("nan"))
    p_old = res_old.pvalues.get("FRxGESpread_old", float("nan"))
    p_new = res_new.pvalues.get("FRxGESpread_new", float("nan"))
    print(f"{'Beta FR-GE':<25}{beta_old:>15.5f}{beta_new:>15.5f}")
    print(f"{'P-value FR-GE':<25}{p_old:>15.5f}{p_new:>15.5f}")

    # Comparar fitted/residuos no periodo comum (ultimos 60 dias uteis)
    common_dates = df_old["date_column"].isin(df_new["date_column"])
    tail_old = df_old[common_dates].tail(60).reset_index(drop=True)
    fitted_old = res_old.predict(sm.add_constant(tail_old[["RealSpread", "GEvsPer", "STSpread", "FRxGESpread_old"]]))

    common_dates_new = df_new["date_column"].isin(df_old["date_column"])
    tail_new = df_new[common_dates_new].tail(60).reset_index(drop=True)
    fitted_new = res_new.predict(sm.add_constant(tail_new[["RealSpread", "GEvsPer", "STSpread", "FRxGESpread_new"]]))

    fit_diff = (fitted_old.values - fitted_new.values)
    print(f"\nFitted EUR (ultimos 60 dias uteis comuns) - dif media absoluta: {abs(fit_diff).mean():.5f}")
    print(f"Fitted EUR - dif maxima absoluta: {abs(fit_diff).max():.5f}")


if __name__ == "__main__":
    main()
