"""
Analise pontual: o que explicou o EUR fitted (modelo) cair em abril de 2025?
Decompoe a variacao do fitted no periodo pelas contribuicoes marginais
(beta_i * delta_X_i) de cada variavel explicativa.

Script auxiliar, nao faz parte do pipeline principal.
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
    ".FFRGE10Y U Index",
]

RENAME = {
    "EUR Curncy": "EURSpot",
    ".GEUSREAL U Index": "RealSpread",
    ".GEVSPER U Index": "GEvsPer",
    ".EUXUS2Y U Index": "STSpread",
    ".FFRGE10Y U Index": "FRxGESpread",
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


def main():
    print("Baixando dados da Bloomberg...")
    data = load_data()

    model_cols = ["EURSpot", "RealSpread", "GEvsPer", "STSpread", "FRxGESpread"]
    data = data.dropna(subset=model_cols)

    y = data["EURSpot"]
    X = data[["RealSpread", "GEvsPer", "STSpread", "FRxGESpread"]]
    X = sm.add_constant(X)

    model = sm.OLS(y, X, missing="drop")
    results = model.fit()
    betas = results.params

    data = data.copy()
    data["fitted"] = results.predict(X)

    # Janela de analise: abril/2025 (e um pouco de contexto antes/depois)
    window = data[(data["date_column"] >= "2025-03-20") & (data["date_column"] <= "2025-05-05")].reset_index(drop=True)

    print("\n--- EUR Fitted x Spot, marco-maio/2025 (semanal) ---")
    weekly = window.iloc[::5]
    print(weekly[["date_column", "EURSpot", "fitted", "RealSpread", "GEvsPer", "STSpread", "FRxGESpread"]].to_string(index=False))

    # Foco no mes de abril
    april = data[(data["date_column"] >= "2025-04-01") & (data["date_column"] <= "2025-04-30")].reset_index(drop=True)
    if april.empty:
        print("Sem dados para abril/2025.")
        return

    start_row = april.iloc[0]
    end_row = april.iloc[-1]

    print(f"\n--- Decomposicao da variacao do FITTED em abril/2025 ---")
    print(f"Data inicial: {start_row['date_column'].date()}  fitted={start_row['fitted']:.4f}  EURSpot={start_row['EURSpot']:.4f}")
    print(f"Data final:   {end_row['date_column'].date()}  fitted={end_row['fitted']:.4f}  EURSpot={end_row['EURSpot']:.4f}")
    total_delta_fitted = end_row["fitted"] - start_row["fitted"]
    total_delta_spot = end_row["EURSpot"] - start_row["EURSpot"]
    print(f"\nDelta fitted (fim - inicio): {total_delta_fitted:+.4f}")
    print(f"Delta EUR spot (fim - inicio): {total_delta_spot:+.4f}")

    var_labels = {
        "RealSpread": "RealSpread (.GEUSREAL)",
        "GEvsPer": "GEvsPer (.GEVSPER)",
        "STSpread": "STSpread (.EUXUS2Y)",
        "FRxGESpread": "FRxGESpread (.FFRGE10Y)",
    }

    print("\nContribuicao de cada variavel para o delta do fitted (beta * delta_X):")
    total_contrib = 0
    for col, label in var_labels.items():
        delta_x = end_row[col] - start_row[col]
        beta = betas[col]
        contrib = beta * delta_x
        total_contrib += contrib
        print(f"  {label:<28} delta_X={delta_x:+.4f}  beta={beta:+.4f}  contrib={contrib:+.4f}")
    print(f"  {'Soma das contribuicoes':<28} {'':>9}  {'':>9}  contrib={total_contrib:+.4f}")

    # Tambem mostra dia a dia o fitted, pra identificar quando comecou a cair
    print("\n--- Fitted diario em abril/2025 ---")
    print(april[["date_column", "fitted", "EURSpot", "RealSpread", "GEvsPer", "STSpread", "FRxGESpread"]].to_string(index=False))


if __name__ == "__main__":
    main()
