# 展示素材

- 动画：[中文](flowtest-zh-CN.gif) · [English](flowtest-en.gif)
- 静态图：[中文](flowtest-zh-CN-poster.png) · [English](flowtest-en-poster.png)
- 架构图：[中文](architecture.svg) · [English](architecture.en.svg)
- [架构与源码对应](../architecture.md)

动画围绕录制与断言、固定版本和环境快照、保留首次失败三个场景展开。浅立体模块使用靛蓝表达流程与证据，绿色表示断言通过，琥珀色保留重试后通过的状态。画面使用示例数据。

## 重新生成

安装 `requirements.txt` 中的 Pillow，在仓库根目录执行：

```sh
python docs/media/render.py             # 中英文两版
python docs/media/render.py --lang zh-CN
python docs/media/render.py --lang en
```

脚本使用本机字体，不分发字体文件。中文默认使用 Windows 微软雅黑；其他系统可通过 `FLOWTEST_MEDIA_FONT` 指定支持中文的字体路径。输出为 1120 × 640，采用二倍采样和统一调色板。
