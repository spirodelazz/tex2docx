# tex2docx

简体中文 | [English](README.en.md)

一键将 LaTeX 手稿(IEEE 及同类格式)转换为 Word (.docx) 文档,表格、公式、图片、算法和参考文献全部保留。

## 项目简介(About)

写论文时经常会遇到"期刊要求提交 Word 版手稿"的情况——手动把 LaTeX 转成 Word 往往意味着重新排版公式、逐张插图、逐条编号。本项目把整个转换过程封装成**一条命令**:内部通过 pandoc 完成 LaTeX → Word 的主体转换,再用一个基于 panflute 的 AST 过滤器补齐 pandoc 做不到的部分——浮动体的图表编号与交叉引用解析、PDF 图片光栅化、IEEE 特有结构(标题块、摘要、关键词、作者简介)的还原。

项目源自实际投稿需求,以一篇完整的 IEEE 手稿(24 个编号公式、19 张图、7 张表、5 个算法、3 份作者简介)做过端到端验证,转换全程零警告。

## 环境要求

- [pandoc](https://pandoc.org/installing.html) 3.0 及以上(加入 `PATH`,或通过 `PANDOC` 环境变量指向可执行文件;AST 过滤器依赖 pandoc 3 的文档结构)
- Python 3.10+,外加两个依赖包:

  ```bash
  pip install panflute pymupdf
  ```

## 使用方法

```bash
python convert.py path/to/manuscript.tex -o output.docx
```

`convert.py` 运行四段管线:

| 阶段 | 工具 | 作用 |
|------|------|------|
| 1 | `preprocess.py` | 将 LaTeX 源码规范化为 `build/clean.tex`,并把图表/公式标签记录到 `build/labels.json` |
| 2 | pandoc | LaTeX → JSON AST |
| 3 | `filter_latex2docx.py` | 图表编号、题注加前缀("Figure 1: ...")、解析 `\ref`/`\autoref`/`\eqref`、PDF 图光栅化为 PNG(PyMuPDF,带缓存) |
| 4 | pandoc | JSON → .docx,`--citeproc` 引用处理(内置 `ieee.csl`),参考文献自动探测,样式取自 `template.docx` |

参数:`--bibliography`、`--csl`、`--template`、`--resource-dir`、`--build-dir`、`--no-macros`。`bash.sh` / `bash.bat` 只是转发参数到 `convert.py` 的薄封装。

## 支持范围

- 表格(带题注和编号)
- 展示公式(自动编号 `(1)`、`(2)`、…;`\eqref` 解析为 `(N)`)
- 图片(PDF 图按 1024 px 光栅化为 PNG;多子图浮动体和 `figure*` 跨栏环境均保留)
- `algorithm`/`algorithmic` 伪代码(降级为 "Algorithm N" 标题加 verbatim 代码块)
- IEEE 特有结构:标题块、摘要、`\IEEEPARstart`、关键词、`IEEEbiography`(照片 + 加粗姓名段落)
- 引用经 `--citeproc` 处理为 IEEE 数字编号 `[1]`,参考文献表附于文末

## 项目结构

```
convert.py               一键管线入口
preprocess.py            阶段 1:LaTeX 规范化 + 标签清单
filter_latex2docx.py     阶段 3:pandoc AST 后处理(panflute)
template.docx            Word 样式模板(reference-doc)
ieee.csl                 citeproc 的 IEEE 引用样式
manuscript/              用于测试的 IEEE 手稿样例(不随仓库分发)
bash.sh / bash.bat       convert.py 的薄封装
```

中间文件位于 `build/`(已被 git 忽略):`clean.tex`、`labels.json`、`ast.json`、`filtered.json`。

## 参数说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `-o, --output` | 与输入 tex 同目录 | 输出 .docx 路径 |
| `--bibliography` | 自动探测(输入同目录的 `<输入名>.bib` 或 `ref.bib`) | 交给 citeproc 的参考文献文件 |
| `--csl` | 内置 `ieee.csl` | 引用样式 |
| `--template` | 内置 `template.docx` | Word 样式模板;换成自己的即可套用自定义样式 |
| `--resource-dir` | 输入文件所在目录 | 图片/参考文献所在目录 |
| `--build-dir` | `build/` | 中间文件暂存目录 |
| `--no-macros` | 关闭 | 跳过内置的 vanvliet 宏展开表(`\mat`、`\tcov` 等) |

## 故障排查

- **`pandoc not found`** — 安装 pandoc,或设置 `PANDOC` 环境变量指向可执行文件完整路径:`PANDOC=/path/to/pandoc python convert.py ...`
- **`ModuleNotFoundError: panflute` / `pymupdf`** — 用装了依赖的解释器运行管线,或执行 `pip install panflute pymupdf`。
- **`unresolved reference: \ref{...}`** — 该标签没有对应的图、表或公式;检查手稿中标签拼写。输出中该位置会保留标签文字。
- **`PDF image not found`** — 图片必须位于 `--resource-dir`(默认为输入 tex 所在目录),且与 `\includegraphics` 中的路径一致。
- **引用显示为原始 key** — 没找到 `.bib` 文件;请传 `--bibliography path/to/refs.bib`。

## 已知限制

- 标题区的 `\thanks` 脚注(基金/单位信息)会被丢弃;作者单位仅在 `IEEEbiography` 条目中保留。
- 算法伪代码是纯 verbatim 文本:其中的数学式保留 LaTeX 源码形式,行缩进不保留。
- 首页版式(IEEE 双栏标题区)不复原;标题块为居中加粗段落。
- 分页/分栏与浮动体位置(`[!htb]`)由 Word 自动排版,不逐字复现。
