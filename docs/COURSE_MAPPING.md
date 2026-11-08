# Course mapping

Reference: kioca_files/T01-DataAnalyticshssong-ENG.pptx, Chapters 08 and 09. Example identifiers below were verified in that deck. The live registry and actual source are in backend/app/charts.py.

| Chapter | Topic | Verified example | Python function | Climate use | Builder |
|---|---|---|---|---|---|
| 08 Advanced Data Visualization | Seaborn bar graph | Code 8-1 | sns.barplot() | Compare complete-month rainfall | build_bar |
| 08 | Seaborn line graph | Code 8-2 | sns.lineplot() | Display filtered historical trends | build_line |
| 08 | Seaborn box plot | Code 8-3 | sns.boxplot() | Compare daily climate distributions | build_box |
| 08 | Bubble chart | Code 8-4 | sns.scatterplot() | Pair temperature and rain; wind controls size | build_bubble |
| 08 | Rainfall–wind extension | Seaborn scatter concept | sns.scatterplot() | Inspect paired rain and wind observations | build_rain_wind |
| 08 | Mosaic plot | Deck mosaic section | statsmodels mosaic() | Compare rainy and dry observed days | build_mosaic |
| 08 | Heatmap | Code 8-6 | pivot_table(), sns.heatmap() | Read rainfall seasonality | build_heatmap |
| 08 | Treemap | Code 8-8 | px.treemap() | Show additive rainy-day counts | build_treemap |
| 09 Map Data Analysis | Markers and map symbols | Code 9-1, 9-3, 9-6 | folium.Map(), Marker(), CircleMarker() | Map filtered climate summaries | build_map |

The deck's earlier Seaborn ci=None parameter is replaced with errorbar=None. Machine-specific source paths are replaced with validated local data or private uploads. Folium produces browser HTML. The code modal displays the exact builder and preparation function used for the current filters.
