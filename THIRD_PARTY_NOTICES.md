# Third-Party Notices

本项目通过 optional extras（`pip install ".[ml]"`）引入以下第三方依赖，
仅用于 SmartRouter 离线训练与可选精判；不安装时主进程不加载、功能自动禁用。

| 依赖 | 版本要求 | 许可证 | 用途 |
|---|---|---|---|
| numpy | >=1.26 | BSD-3-Clause | 数值数组基础 |
| scikit-learn | >=1.4 | BSD-3-Clause | TF-IDF 特征向量化 |
| LightGBM | >=4.0 | MIT | 梯度提升树分类器（路由精判） |
| joblib | >=1.3 | BSD-3-Clause | 训练产物（vectorizer + model）序列化 |

各许可证原文见官方仓库：
- numpy: https://github.com/numpy/numpy/blob/main/LICENSE.txt
- scikit-learn: https://github.com/scikit-learn/scikit-learn/blob/main/COPYING
- LightGBM: https://github.com/microsoft/LightGBM/blob/master/LICENSE
- joblib: https://github.com/joblib/joblib/blob/main/LICENSE.txt
