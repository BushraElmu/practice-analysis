# practice-analysis

A deliberate-practice repository for building analytical workflow fluency through small, reproducible data-analysis exercises.

The current exercises use a stable Calgary weather dataset so that the analytical problem can change without requiring a new domain or dataset for every repetition.

## What This Practices

Each exercise repeats the same general workflow:

```text
problem
→ prepare data
→ load
→ inspect
→ establish grain and quality
→ clean if warranted
→ transform
→ visualize
→ reproduce
```

The goal is to make ordinary analytical execution increasingly automatic while gradually introducing new transformations, visualizations, data interfaces, and data-quality problems.

## Current Scope

The repository currently focuses on:

- Python
- pandas
- matplotlib
- CSV data
- hourly historical weather data from Open-Meteo / ERA5
- increasingly varied analytical transformations and visualizations

Later exercises will introduce additional data interfaces and controlled data-quality problems as the basic workflow becomes more fluent.

## Repository Structure

```text
practice-analysis/
├── YYYY-MM-DD_brief-exercise-name/
│   ├── prepare_data.py
│   ├── analysis.ipynb
│   ├── README.md
│   ├── pyproject.toml
│   ├── uv.lock
│   ├── data/
│   └── outputs/
├── dev/
├── .gitignore
├── LICENSE
└── README.md
```

Each dated directory is one analytical practice.

`prepare_data.py` creates the reproducible starting data for the exercise.

`analysis.ipynb` performs the analytical work.

Generated data are not committed; final analytical outputs are.

## Practices

| # | Exercise |
|---|---|
| 1 | Average June Temperature, 2024–2026 |
| 2 | Average May Temperature, 2024–2026 |
| 3 | Maximum April Temperature, 2024–2026 |
| 4 | 7-Day Rolling Average Temperature, Final 90 Days |
| 5 | Average Relative Humidity by Hour of Day, August 2025 |
| 6 | Average Daily Temperature Range by Month, 2025 |

## Reproducing an Exercise

From an exercise directory:

```bash
uv sync
uv run python prepare_data.py
uv run --with jupyter jupyter lab
```

Then open and run `analysis.ipynb`.

Individual exercise READMEs contain the analytical question, parameters, method, output, and data attribution.

## Data

Current exercises use historical weather data from the [Open-Meteo](https://open-meteo.com/) Historical Weather API using ERA5 data.

The current weather substrate is hourly Calgary data using `America/Edmonton` local-time semantics.

See the individual exercise README for attribution and exercise-specific details.

## License

Code in this repository is licensed under the Apache License 2.0.

Data retain their respective source licenses and attribution requirements.
