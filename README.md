# 山地流域指标分析

项目保存山地流域的来源版次，按资料取得时间比较单期指标并给出区域规则判断。evidence 保存来源，metrics 计算同口径偏离，context 解释当前区域规则，report 保存报告与归档。seasonal 在此基础上做跨季方案比较：冰川融水估计、来水观测、库容与灌溉需求各按自己的时间尺度与来源版本参与计算，区分“估计口径改变”与“承诺变化”，并指出受影响月份。历史报告按归档正文复现，不用新估计覆盖旧决定。样本是合成数据。

Python 3.11 以上，在项目根目录执行：

    python -m unittest discover -s tests -v
    python -m compileall -q climate_analysis tests
    python -m climate_analysis.cli inspect data/example.json

跨季方案比较（可加 `--db FILE` 持久化归档，供 `history`/`replay` 使用）：

    python -m climate_analysis.cli seasonal --input data/seasonal.json
    python -m climate_analysis.cli history --db basin.db --kind seasonal
    python -m climate_analysis.cli replay --db basin.db <report_id>

Dockerfile 可在 Linux 容器运行：docker build -t climate-analysis-005 .；docker run --rm climate-analysis-005。
