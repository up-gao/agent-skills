# skill-fetch-wx-knowledge 使用说明

微信公众号文章采集技能。根据链接类型自动选择处理方式，支持三种场景。

## 场景一：单篇文章采集

**适用于：** 朋友分享的一篇文章、想收藏的技术干货、需要离线阅读的公众号内容。

**链接特征：** `mp.weixin.qq.com/s/...`

**使用方式：**

```
# 最简用法 — 只给链接，默认保存到当前目录
https://mp.weixin.qq.com/s/1LqWAGfdocJpFdAlbAJDeg 采集这篇

# 指定输出目录
保存这个微信文章 https://mp.weixin.qq.com/s/xxxxx 到 ./notes/

# 指定输出目录和图片目录
采集 https://mp.weixin.qq.com/s/xxxxx
  输出到 /Users/me/docs/wechat/
  图片放到 /Users/me/docs/.img/wechat/

# 其他常见说法
下载这篇公众号文章 https://mp.weixin.qq.com/s/xxxxx
提取这篇微信文章的内容 https://mp.weixin.qq.com/s/xxxxx
把 https://mp.weixin.qq.com/s/xxxxx 转成 Markdown 保存
```

**输出示例：**

```
notes/
└── 多平台内容自动上传工具.md     ← 标题自动作为文件名
images/                            ← 默认图片目录
├── image-20260528104203839.jpeg
└── image-20260528104203848.png
```

```markdown
# 11.4k Star，多平台内容自动上传

> 原文链接：https://mp.weixin.qq.com/s/xxxxx

正文内容...

![配图](/绝对路径/images/image-20260528104203839.jpeg)
```

---

## 场景二：专栏/合集采集

**适用于：** 公众号作者整理的系列文章、面试题合集、教程专栏，需要一次性全部下载。

**链接特征：** `mp.weixin.qq.com/mp/appmsgalbum?...` 或包含 `action=getalbum`

**使用方式：**

```
# 最简用法 — 采集整个专栏
采集这个专栏 https://mp.weixin.qq.com/mp/appmsgalbum?__biz=...&action=getalbum&album_id=...

# 指定目录
把这个专栏全部下载下来 https://mp.weixin.qq.com/mp/appmsgalbum?...
  保存到 ./go-interview/

# 其他常见说法
这个合集帮我全部采集 https://mp.weixin.qq.com/mp/appmsgalbum?...
导出这个专栏的所有文章 https://mp.weixin.qq.com/mp/appmsgalbum?...
```

**处理过程：**
1. 抓取专栏页面，自动提取所有文章链接
2. 逐篇采集，每篇间隔 2-3 秒（避免反爬）
3. 专栏内所有文章共享一个图片目录（图片自动去重）
4. 每篇文章独立一个 `.md` 文件

**输出示例：**

```
go-interview/
├── 什么是GMP调度模型.md
├── Go内存逃逸分析.md
├── Channel底层实现原理.md
├── ...（共 10 篇）
└── images/
    ├── image-20260730233221331.png
    ├── image-20260730233222145.jpeg
    └── ...（共 156 张）
```

**汇总报告示例：**

```
专栏采集完成: Go后端面试题合集
─────────────────────────────
成功: 10/10 篇
总图片: 156 张, 45 MB
输出目录: /Users/me/docs/go-interview/
```

---

## 场景三：批量多篇采集

**适用于：** 有多个独立的文章链接（不同文章、不同公众号），想一次性全部下载。

**链接特征：** 多个 `mp.weixin.qq.com/s/...` URL

**使用方式：**

```
# 多篇一起采
这几篇微信文章帮我全部下载：
https://mp.weixin.qq.com/s/xxxxx
https://mp.weixin.qq.com/s/yyyyy
https://mp.weixin.qq.com/s/zzzzz

# 指定输出目录
批量采集这些文章到 ./wx-collection/：
https://mp.weixin.qq.com/s/xxxxx
https://mp.weixin.qq.com/s/yyyyy
```

**特点：**
- 所有文章共享图片目录（相同图片只下载一次）
- 每篇文章独立的 `.md` 文件
- 最后汇总成功/失败数量

---

## 场景判断速查

| 你给的链接 | 自动识别为 | 处理方式 |
|-----------|-----------|---------|
| `/s/...` 一个 | 单篇 | 直接采集 |
| `/s/...` 多个 | 批量 | 循环逐篇 |
| `/mp/appmsgalbum?...` | 专栏 | 先提取列表再逐篇 |
| 混合（专栏 + 单篇） | 混合 | 先专栏后单篇 |

## 参数说明

| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| URL | 是 | — | 文章链接、专栏链接或链接列表 |
| 输出目录 | 否 | 当前工作目录 | Markdown 文件保存位置 |
| 图片目录 | 否 | `<输出目录>/images/` | 静态资源存放位置 |

## 输出约定

- **文件命名**：以文章标题作为文件名（保留中文，过长截断）
- **原文链接**：每篇 Markdown 标题下方自动插入 `> 原文链接：...`
- **图片命名**：`image-YYYYMMDDHHmmssSSS.ext`（时间戳，唯一且可排序）
- **图片路径**：Markdown 中使用绝对路径，文件移动后图片不受影响
