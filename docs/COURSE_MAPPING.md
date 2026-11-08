# Course mapping

Reference: kioca_files/T01-DataAnalyticshssong-ENG.pptx, Chapters 08 and 09. Example identifiers below were checked in that deck. The live registry and source are in backend/app/charts.py.

| Chapter | Topic | Verified example | Actual function | Learning objective | Builder |
|---|---|---|---|---|---|
| 08 Advanced Data Visualization | Seaborn bar graph | Code 8-1 | sns.barplot() | Compare grouped facility counts | build_bar |
| 08 | Seaborn line graph | Code 8-2 | sns.lineplot() | Compare annual means | build_line |
| 08 | Seaborn box plot | Code 8-3 | sns.boxplot() | Read distributions | build_box |
| 08 | Bubble chart | Code 8-4 | sns.scatterplot() | Compare two variables and a third size | build_bubble |
| 08 | Mosaic plot | Deck mosaic section | statsmodels mosaic() | Compare categorical composition | build_mosaic |
| 08 | Heatmap | Code 8-6 | pivot_table(), sns.heatmap() | Read rainfall seasonality | build_heatmap |
| 08 | Treemap | Code 8-8 | px.treemap() | Read an additive hierarchy | build_treemap |
| 09 Map Data Analysis | Area, markers, map symbols | Code 9-1, 9-3, 9-6 | folium.Map(), Marker(), CircleMarker() | Locate samples and mapped features | build_map |

The deck's bar example uses ci=None, which current Seaborn replaces with errorbar=None. The deck's Windows file paths are machine-specific, so this app loads validated local snapshots or session uploads. Folium produces HTML for the browser instead of opening a desktop helper. Treemap tile area uses additive facility counts. The code modal displays the actual builder and preparation function, paired with current filters.
