# 率定搜索范围来源与适配（2026-10-15核对）

模板只替换搜索上下限，保留初值与参与搜索的勾选项。项目原有9个产流搜索变量的建议范围作为默认值；物理允许范围独立校验。取消勾选时保留已编辑范围。初值超出范围时提示，不自动截断。始终保留WM > WUM + WLM、日—小时水源系数转换和汇流稳定性校验。

| 模板 | 原始研究与范围 | 本项目适配 |
| --- | --- | --- |
| 文献常规范围 | Wu, Q., Liu, S., Cai, Y., Li, X., Jiang, Y. (2017). Improvement of hydrological model calibration by selecting multiple parameter ranges. Hydrology and Earth System Sciences, 21, 393–407. DOI: 10.5194/hess-21-393-2017。表3初始范围：WM 120–200、B 0.1–0.4、SM 10–50、EX 1–1.5、IM 0.01–0.04。 | 广西潮田河476.24 km²，日尺度研究。仅覆盖这5个能够对应的变量。其余沿用项目建议范围。 |
| 文献扩展范围 | Gui, Z., Zhang, F., Yue, K., Lu, X., Chen, L., Wang, H. (2024). Identifying and Interpreting Hydrological Model Structural Nonstationarity Using the Bayesian Model Averaging Method. Water, 16, 1126. DOI: 10.3390/w16081126。表2：B 0.1–3、SM 1–80、EX 0.7–2、IMP 0.0005–0.1。 | 美国MOPEX与澳大利亚三个流域，面积990–4828 km²，日尺度新安江/GR4J模型比较。IMP对应本项目IM。仅覆盖这4项，未复制原文KI/KG或不同汇流结构的参数。 |

两套范围均不代表徐畈流域最优值。原文全文已核对，检索关键词为“Xinanjiang parameter ranges 0.0005 0.7”，并直接读取出版方PDF表格：

- https://hess.copernicus.org/articles/21/393/2017/hess-21-393-2017.pdf
- https://mdpi-res.com/d_attachment/water/water-16-01126/article_deploy/water-16-01126.pdf

本地核对副本在`开发产物/hess-21-393-2017.pdf`和`开发产物/water-16-01126.pdf`。不纳入业务输入、科学配置或Git源码。

Numba兼容性核对：官方0.60安装文档的依赖支持表列出Python 3.9–3.12、NumPy 2.0、llvmlite 0.43.x组合：https://numba.readthedocs.io/en/0.60.0/user/installing.html 。本项目实际环境为Python 3.9.17、NumPy 2.0.2，锁定Numba 0.60.0和llvmlite 0.43.0；离线wheel位于`开发产物/offline-wheels-py39`。
