from __future__ import annotations

import argparse
import base64
import io
import math
from datetime import datetime
from pathlib import Path

import pandas as pd
from PIL import Image, ImageDraw, ImageFont


PIE_COLORS = [
  "#0F7A79", "#6DC1A2", "#F4B400", "#D8A24B", "#7D8A96",
  "#B7C4CF", "#9BB7B0", "#C9D3A8", "#D7B98E", "#A7C7C4",
  "#C6CEC8", "#8EA6A3", "#E1D0A8", "#B8C8B8", "#D8DDDF",
]


def _parse_args() -> argparse.Namespace:
  script_dir = Path(__file__).resolve().parent
  default_input = script_dir / "Lenovo Intel Connectivity JIRA Data Center 2023-2025.xlsx"
  default_output = script_dir / "outputs" / "lenovo_component_pie_2023_2025_report.html"

  parser = argparse.ArgumentParser(
    description=(
      "Generate a year-by-year component pie chart HTML report from "
      "Lenovo Intel Connectivity JIRA Data Center Excel data."
    )
  )
  parser.add_argument("--input", type=Path, default=default_input, help="Path to the source Excel file.")
  parser.add_argument("--output", type=Path, default=default_output, help="Path to the generated HTML report.")
  parser.add_argument("--sheet", default="general_report", help="Excel sheet name.")
  parser.add_argument("--start-year", type=int, default=2023, help="Inclusive start year.")
  parser.add_argument("--end-year", type=int, default=2025, help="Inclusive end year.")
  return parser.parse_args()


def _load_dataframe(path: Path, sheet: str) -> pd.DataFrame:
  df = pd.read_excel(path, sheet_name=sheet, header=3)
  df = df.rename(columns=lambda c: str(c).strip())
  required_columns = {"Created", "Component/s"}
  missing = required_columns - set(df.columns)
  if missing:
    raise ValueError(f"Missing required columns: {sorted(missing)}")
  return df


def _to_year(created_series: pd.Series) -> pd.Series:
  parsed = pd.to_datetime(created_series, format="%d/%b/%y %I:%M %p", errors="coerce")
  if parsed.notna().sum() < int(len(created_series) * 0.8):
    parsed = pd.to_datetime(created_series, errors="coerce")
  return parsed.dt.year


def _clean_component(component_series: pd.Series) -> pd.Series:
  cleaned = component_series.fillna("(missing)").astype(str).str.strip()
  cleaned = cleaned.replace({"": "(missing)", "NA": "(missing)", "nan": "(missing)"})
  return cleaned


def _build_chart_data(df: pd.DataFrame, start_year: int, end_year: int) -> dict[str, dict[str, object]]:
  work = df[["Created", "Component/s"]].copy()
  work["year"] = _to_year(work["Created"])
  work["component"] = _clean_component(work["Component/s"])
  work = work[work["year"].between(start_year, end_year, inclusive="both")]

  chart_data: dict[str, dict[str, object]] = {}
  for year in sorted(work["year"].dropna().astype(int).unique()):
    subset = work[work["year"] == year]
    counts = (
      subset["component"]
      .value_counts(dropna=False)
      .rename_axis("component")
      .reset_index(name="count")
      .sort_values(by=["count", "component"], ascending=[False, True])
    )
    if counts.empty:
      chart_data[str(year)] = {
        "labels": ["No Data"],
        "values": [1],
        "total": 0,
        "rows": [{"component": "No Data", "count": 0, "pct": 0.0}],
      }
      continue

    total = int(counts["count"].sum())
    rows = []
    for _, row in counts.iterrows():
      cnt = int(row["count"])
      rows.append(
        {
          "component": str(row["component"]),
          "count": cnt,
          "pct": round((cnt / total) * 100.0, 2) if total > 0 else 0.0,
        }
      )

    chart_data[str(year)] = {
      "labels": [r["component"] for r in rows],
      "values": [r["count"] for r in rows],
      "total": total,
      "rows": rows,
    }

  return chart_data


def _render_pie_data_uri(values: list[int]) -> str:
  size = 560
  margin = 24
  image = Image.new("RGB", (size, size), "white")
  draw = ImageDraw.Draw(image)
  font = ImageFont.load_default()

  total = sum(int(v) for v in values)
  if total <= 0:
    draw.text((size // 2 - 20, size // 2 - 6), "No Data", fill="#333333", font=font)
  else:
    bbox = (margin, margin, size - margin, size - margin)
    start_angle = -90.0
    for idx, raw in enumerate(values):
      value = int(raw)
      sweep = (value / total) * 360.0
      color = PIE_COLORS[idx % len(PIE_COLORS)]
      draw.pieslice(bbox, start=start_angle, end=start_angle + sweep, fill=color, outline="white", width=2)

      pct = (value / total) * 100.0
      label = f"{pct:.1f}%"
      mid_rad = math.radians(start_angle + (sweep / 2.0))

      if sweep >= 10:
        cx = size / 2 + math.cos(mid_rad) * (size * 0.26)
        cy = size / 2 + math.sin(mid_rad) * (size * 0.26)
        draw.text((cx - 8, cy - 6), label, fill="#333333", font=font)
      else:
        edge_x = size / 2 + math.cos(mid_rad) * (size * 0.46)
        edge_y = size / 2 + math.sin(mid_rad) * (size * 0.46)
        text_x = size / 2 + math.cos(mid_rad) * (size * 0.57)
        text_y = size / 2 + math.sin(mid_rad) * (size * 0.57)
        draw.line((edge_x, edge_y, text_x, text_y), fill="#666666", width=1)
        draw.text((text_x - 6, text_y - 6), label, fill="#333333", font=font)
      start_angle += sweep

  buffer = io.BytesIO()
  image.save(buffer, format="PNG")
  encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
  return f"data:image/png;base64,{encoded}"


def _build_html(chart_data: dict[str, dict[str, object]], src_file: Path, start_year: int, end_year: int) -> str:
  generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

  year_sections = []
  for year, payload in chart_data.items():
    rows = payload["rows"]  # type: ignore[index]
    values = payload["values"]  # type: ignore[index]
    pie_data_uri = _render_pie_data_uri(values)
    table_rows = "\n".join(
      f"<tr><td>{r['component']}</td><td>{r['count']}</td><td>{r['pct']}%</td></tr>" for r in rows
    )
    year_sections.append(
      (
        f"<div class='year-section'>"
        f"<h2>{year} - Component/s Issue Count</h2>"
        f"<div class='grid'>"
        f"<div class='card'><h3>{year} Pie Chart</h3><div class='pie-wrap'><img class='pie-img' src='{pie_data_uri}' alt='{year} component pie chart' /></div></div>"
        f"<div class='card'><h3>{year} Breakdown Table</h3>"
        f"<table><thead><tr><th>Component/s</th><th>Issue Count</th><th>%</th></tr></thead><tbody>{table_rows}</tbody></table>"
        f"</div></div></div>"
      )
    )

  return f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
  <title>Lenovo Component/s Pie Report ({start_year}-{end_year})</title>
  <style>
  body {{
    font-family: 'Segoe UI', Inter, -apple-system, BlinkMacSystemFont, sans-serif;
    margin: 24px;
    color: #333333;
    background: #f7f8fa;
  }}
  h1, h2, h3 {{ margin: 8px 0; color: #333333; font-weight: 600; }}
  .meta {{ color: #666666; margin-bottom: 18px; }}
  .year-section {{ margin-top: 18px; border-top: 2px solid #e5e7eb; padding-top: 12px; }}
  .grid {{ display: grid; grid-template-columns: repeat(2, minmax(320px, 1fr)); gap: 16px; }}
  .card {{
    border: 1px solid #e5e7eb;
    border-radius: 12px;
    padding: 12px;
    background: #ffffff;
    box-shadow: 0 4px 18px rgba(15, 23, 42, 0.04);
  }}
  .pie-wrap {{ width: 100%; max-width: 420px; margin: 0 auto; }}
  .pie-img {{ width: 100%; height: auto; display: block; border-radius: 8px; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 10px; }}
  th, td {{ border: 1px solid #d1d5db; padding: 8px; text-align: center; }}
  th {{ background: #f3f4f6; color: #333333; }}
  </style>
</head>
<body>
  <h1>Lenovo Component/s Issue Count - Yearly Pie Charts ({start_year}-{end_year})</h1>
  <div class='meta'>Generated at: {generated_at} | Source: {src_file.name} | Metric: issue count by Component/s | Rendering: offline static images</div>
  {''.join(year_sections)}
</body>
</html>
"""


def main() -> None:
  args = _parse_args()
  args.output.parent.mkdir(parents=True, exist_ok=True)

  df = _load_dataframe(args.input, args.sheet)
  chart_data = _build_chart_data(df, args.start_year, args.end_year)
  if not chart_data:
    raise ValueError(
      f"No rows found for year range {args.start_year}-{args.end_year} in {args.input.name}."
    )

  html = _build_html(chart_data, args.input, args.start_year, args.end_year)
  args.output.write_text(html, encoding="utf-8")
  print(f"Generated: {args.output}")


if __name__ == "__main__":
  main()
