import pathlib
import webbrowser
from datetime import datetime

import blpapi
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import statsmodels.api as sm

TICKERS = [
    "EUR Curncy",
    ".GEUSREAL U Index",
    ".GEVSPER U Index",
    ".EUXUS2Y U Index",
    ".FFRGE10Y U Index",
]
START_DATE = "2021-04-01"
BBG_FIELD = "PX_LAST"


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


def load_data_from_bloomberg(start_date=START_DATE):
    end_date = pd.Timestamp.today().strftime("%Y-%m-%d")

    try:
        data = _load_with_blpapi(start_date, end_date)
    except Exception as exc:
        raise RuntimeError(
            "Unable to fetch Bloomberg data through blpapi. "
            "Make sure Bloomberg Terminal is running and API is available on localhost:8194. "
            f"Last error: {exc}"
        ) from exc

    expected = {
        "EUR Curncy": "EURSpot",
        ".GEUSREAL U Index": "RealSpread",
        ".GEVSPER U Index": "GEvsPer",
        ".EUXUS2Y U Index": "STSpread",
        ".FFRGE10Y U Index": "FRxGESpread",
    }

    missing = [c for c in expected if c not in data.columns]
    if missing:
        raise KeyError(f"Bloomberg data is missing expected tickers: {missing}")

    data = data.rename(columns=expected)
    data["date_column"] = pd.to_datetime(data["date_column"], errors="coerce")
    data = data.dropna(subset=["date_column"]).sort_values("date_column")

    model_cols = ["EURSpot", "RealSpread", "GEvsPer", "STSpread", "FRxGESpread"]
    data = data.dropna(subset=model_cols)

    # Ensure every business day between start and today has a row
    bday_index = pd.bdate_range(start=data["date_column"].min(), end=pd.Timestamp.today().normalize())
    data = (
        data.set_index("date_column")
        .reindex(bday_index)
        .ffill()
        .reset_index()
        .rename(columns={"index": "date_column"})
    )
    data = data.dropna(subset=model_cols)

    return data


def _now_brasilia():
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/Sao_Paulo"))
    except Exception:
        # Fallback: assumes the machine's local time is already Brasília
        return datetime.now()


def main(output_path="eur_model_report.html", open_browser=True):
    data = load_data_from_bloomberg()

    regression_data = pd.DataFrame(
        {
            "date_column": data["date_column"],
            "dependent_variable": data["EURSpot"],
            "independent_variable1": data["RealSpread"],
            "independent_variable2": data["GEvsPer"],
            "independent_variable3": data["STSpread"],
            "independent_variable4": data["FRxGESpread"],
        }
    )

    print("Summary of regression_data:")
    print(regression_data.describe(include="all"))

    y = regression_data["dependent_variable"]
    X = regression_data[
        [
            "independent_variable1",
            "independent_variable2",
            "independent_variable3",
            "independent_variable4",
        ]
    ]
    X = sm.add_constant(X)

    model = sm.OLS(y, X, missing="drop")
    results = model.fit()
    print("\nRegression summary:")
    print(results.summary())

    eur_fitted = results.predict(X)

    fitted_frame = pd.DataFrame(
        {
            "date_model": regression_data["date_column"],
            "eur_fitted": eur_fitted,
            "EURSpot": data["EURSpot"],
        }
    )

    fitted_frame["date_model"] = pd.to_datetime(fitted_frame["date_model"], errors="coerce")

    residuals = results.resid
    res_df = pd.DataFrame(
        {
            "date_res": pd.to_datetime(regression_data["date_column"], errors="coerce"),
            "residuals": residuals,
        }
    )

    betas = results.params

    fig_fit = go.Figure()
    fig_fit.add_trace(
        go.Scatter(
            x=fitted_frame["date_model"],
            y=fitted_frame["eur_fitted"],
            name="Fitted Euro",
            line=dict(color="red"),
        )
    )
    fig_fit.add_trace(
        go.Scatter(
            x=fitted_frame["date_model"],
            y=fitted_frame["EURSpot"],
            name="Actual Euro Spot",
            line=dict(color="blue"),
        )
    )
    fig_fit.update_layout(
        title="Euro Value Comparison",
        xaxis_title="Date",
        yaxis_title="Eur Value",
        height=420,
        width=980,
        margin=dict(l=60, r=30, t=60, b=50),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )

    fig_res = go.Figure()
    fig_res.add_trace(
        go.Scatter(
            x=res_df["date_res"],
            y=res_df["residuals"],
            name="Model Residual",
            line=dict(color="purple"),
        )
    )
    levels = [0.00, 0.02, 0.04, 0.06, -0.02, -0.04, -0.06, -0.08]
    for level in levels:
        fig_res.add_hline(y=level, line_color="gray", line_width=1, line_dash="dot")

    fig_res.update_layout(
        title="Eur Model Residual",
        xaxis_title="Date",
        yaxis_title="Eur model residual",
        yaxis_range=[-0.08, 0.06],
        height=420,
        width=980,
        margin=dict(l=60, r=30, t=60, b=50),
        showlegend=False,
    )

    last10 = fitted_frame[["date_model", "eur_fitted", "EURSpot"]].tail(10)

    # Marginal contribution chart (last 20 days)
    var_map = {
        "independent_variable1": ("RealSpread",   "#1f77b4"),
        "independent_variable2": ("GEvsPer",      "#ff7f0e"),
        "independent_variable3": ("STSpread",     "#2ca02c"),
        "independent_variable4": ("FRxGESpread",  "#d62728"),
    }
    # Keep only days with real EUR data (value changed vs previous day = not forward-filled)
    real_days = regression_data[regression_data["dependent_variable"].diff() != 0].copy()
    contrib_tail = real_days.tail(21).reset_index(drop=True)
    contrib_dates = contrib_tail["date_column"].iloc[1:].dt.strftime("%d-%b").tolist()
    fig_contrib = go.Figure()
    for var_col, (label, color) in var_map.items():
        beta = betas.get(var_col, 0)
        daily_contrib = (contrib_tail[var_col].diff().iloc[1:] * beta).tolist()
        fig_contrib.add_trace(go.Bar(x=contrib_dates, y=daily_contrib, name=label, marker_color=color))
    fig_contrib.update_layout(
        barmode="relative",
        title="Daily Marginal Contribution to EUR Fitted (last 20 trading days)",
        xaxis=dict(title="Date", type="category"),
        yaxis_title="Contribution to fitted change",
        height=420,
        width=980,
        margin=dict(l=60, r=30, t=60, b=50),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    fig_contrib.add_hline(y=0, line_color="black", line_width=1)

    # Weekly marginal contribution chart (last 10 weeks)
    weekly = (
        regression_data.set_index("date_column")
        .resample("W-FRI")
        .last()
        .dropna()
    )
    # 11 weekly points -> 10 week-over-week changes
    weekly_tail = weekly.tail(11).reset_index()
    weekly_dates = weekly_tail["date_column"].iloc[1:].dt.strftime("%d-%b").tolist()
    fig_contrib_weekly = go.Figure()
    for var_col, (label, color) in var_map.items():
        beta = betas.get(var_col, 0)
        weekly_contrib = (weekly_tail[var_col].diff().iloc[1:] * beta).tolist()
        fig_contrib_weekly.add_trace(go.Bar(x=weekly_dates, y=weekly_contrib, name=label, marker_color=color))
    fig_contrib_weekly.update_layout(
        barmode="relative",
        title="Weekly Marginal Contribution to EUR Fitted (last 10 weeks)",
        xaxis=dict(title="Week ending", type="category"),
        yaxis_title="Contribution to fitted change",
        height=420,
        width=980,
        margin=dict(l=60, r=30, t=60, b=50),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    fig_contrib_weekly.add_hline(y=0, line_color="black", line_width=1)

    def _single_chart(series, title, beta_value):
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=fitted_frame["date_model"], y=series, name=title))
        fig.update_layout(
            title=f"{title} (beta: {beta_value:.4f})",
            xaxis_title="Date",
            yaxis_title="Value",
            height=360,
            width=980,
            margin=dict(l=60, r=30, t=60, b=50),
            showlegend=False,
        )
        return fig

    fig_real = _single_chart(
        regression_data["independent_variable1"],
        "RealSpread",
        betas.get("independent_variable1", float("nan")),
    )
    fig_gevsp = _single_chart(
        regression_data["independent_variable2"],
        "GEvsPer",
        betas.get("independent_variable2", float("nan")),
    )
    fig_st = _single_chart(
        regression_data["independent_variable3"],
        "STSpread",
        betas.get("independent_variable3", float("nan")),
    )
    fig_fr = _single_chart(
        regression_data["independent_variable4"],
        "FRxGESpread",
        betas.get("independent_variable4", float("nan")),
    )
    updated_str = _now_brasilia().strftime("%d/%m/%Y %H:%M")
    html = f"""
    <html>
      <head>
        <meta charset="utf-8" />
        <title>EUR Model Report</title>
        <style>
          body {{ font-family: Arial, sans-serif; margin: 20px; background: #fafafa; }}
          .container {{ max-width: 1040px; margin: 0 auto; }}
          .chart {{ background: #fff; padding: 12px 16px; border-radius: 8px; box-shadow: 0 1px 6px rgba(0,0,0,0.08); margin-bottom: 18px; }}
          .updated {{ color: #555; font-size: 14px; margin: 0 0 18px; }}
          .updated b {{ color: #222; }}
          table {{ border-collapse: collapse; margin-top: 16px; background: #fff; }}
          th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: right; }}
          th {{ background: #f2f2f2; }}
          td:first-child, th:first-child {{ text-align: left; }}
        </style>
      </head>
      <body>
        <div class="container">
          <h2>EUR Model</h2>
          <p class="updated">Última atualização: <b>{updated_str}</b> (horário de Brasília)</p>
          <div class="chart">{pio.to_html(fig_fit, include_plotlyjs="cdn", full_html=False)}</div>
          <div class="chart">{pio.to_html(fig_res, include_plotlyjs=False, full_html=False)}</div>
          <div class="chart">{pio.to_html(fig_contrib, include_plotlyjs=False, full_html=False)}</div>
          <div class="chart">{pio.to_html(fig_contrib_weekly, include_plotlyjs=False, full_html=False)}</div>
          <h3>Last 10 Observations (Fitted vs Actual)</h3>
          {last10.to_html(index=False)}
          <h3>Independent Variables</h3>
          <div class="chart">{pio.to_html(fig_real, include_plotlyjs=False, full_html=False)}</div>
          <div class="chart">{pio.to_html(fig_gevsp, include_plotlyjs=False, full_html=False)}</div>
          <div class="chart">{pio.to_html(fig_st, include_plotlyjs=False, full_html=False)}</div>
          <div class="chart">{pio.to_html(fig_fr, include_plotlyjs=False, full_html=False)}</div>
          <h3>Regression Summary (Betas and P-Values)</h3>
          {results.summary2().tables[1].rename(index={
              "independent_variable1": "RealSpread",
              "independent_variable2": "GEvsPer",
              "independent_variable3": "STSpread",
              "independent_variable4": "FRxGESpread",
          }).to_html()}
        </div>
      </body>
    </html>
    """

    report_file = pathlib.Path(output_path)
    if not report_file.is_absolute():
        report_file = (pathlib.Path(__file__).resolve().parent / report_file).resolve()
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(html, encoding="utf-8")
    if open_browser:
        webbrowser.open(report_file.as_uri())
    return report_file


if __name__ == "__main__":
    main()
