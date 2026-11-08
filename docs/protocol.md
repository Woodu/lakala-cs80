# QDC1 协议与 sidecar 接口

写别的客户端（Linux / 手机 / 单片机）时只需要这一页。设备端只认「QDC1 + BGR565 整帧」，
主机侧用什么语言/框架都随意。

---

## 1. 报文头（32 字节，小端）

| 偏移 | 类型 | 字段 | 说明 |
|---|---|---|---|
| 0 | u32 | magic | `0x31434451`（"QDC1"） |
| 4 | u8 | version | `1` |
| 5 | u8 | kind | 命令字，见下表 |
| 6 | u16 | flags | 命令相关 |
| 8 | u32 | seq | 序号，回复里原样带回 |
| 12 | u32 | offset | 命令相关（帧偏移 / 哈希 / 参数） |
| 16 | u32 | length | payload 字节数 |
| 20 | u32 | raw_length | 解压后长度 |
| 24 | u32 | payload_hash | `XXH32(payload, seed=0)` |
| 28 | u32 | header_hash | `XXH32(header[0..27], seed=0)` |

`XXH32` 是标准 xxHash32（seed 0）。固件校验 `header_hash`，**也校验 `payload_hash`**。

### 回复（设备 → 主机）

同样 32 字节头（`kind=128` ACK、`version=1`、`length=raw_length=24`），后跟 24 字节 body：

`status, receive_us, decode_us, draw_us, free_heap, detail`

`status`：`0` OK / `1` BAD_HEADER / `2` BAD_LENGTH / `3` BAD_HASH / `4` BAD_STATE /
`5` DECODE_ERROR / `6` DISPLAY_ERROR / `7` NO_MEMORY / `8` TIMEOUT。

## 2. 命令

| kind | 命令 | flags | offset | raw_length | payload |
|---|---|---|---|---|---|
| 1 | HELLO | 0 | 0 | 0 | — → `detail=520192`（块大小） |
| 1 | HELLO 特性位 | 2 | — | — | — → `detail=0x23f` |
| 1 | HELLO 屏状态 | 10 | — | — | — → `receive_us=亮度, decode_us=背光, detail=状态位` |
| 1 | HELLO 设亮度 | 11 | 5..100（步长 5） | 0 | — |
| 1 | HELLO 待机/唤醒 | 18 | 1=待机 / 0=唤醒 / 2=查询 | 0 | — |
| 2 | BEGIN | 0 | 0 | **2048000** | — |
| 3 | DATA（整帧 LZ4） | 1 | 0 | 2048000 | LZ4 压缩块 |
| 3 | DATA（裸块 ≤508 KiB） | 0 | 块偏移 | 块长度 | 未压缩像素 |
| 4 | COMMIT | **1** | 整帧像素哈希 | 0 | — |
| 6 | VERIFY | 0 | — | — | — → `detail=framebuffer 的 XXH32` |
| 10 | PATCH（局部） | 1 | 上一帧哈希 | 8+n×1032 | LZ4(8B 头 + n 个 tile) |

局部 patch 的明文结构：`u32 targetHash` + `u32 count` + count ×（`u16 x,u16 y,u16 w,u16 h` + 32×16×2 字节像素），
`x` 必须是 32 的倍数、`y` 必须是 16 的倍数、`w=32,h=16`。

## 3. 四个必踩的坑（实测）

1. **空 payload 的哈希不是 0**，是 `0x02cc5d05`。填 0 会被固件以 `status=3 BAD_HASH` 拒掉。
2. **DATA 不要设 `DEFER_ACK(0x100)`**。不设 → 固件逐块回 ACK（天然限流）；设了 → 反而不回 ACK，主机卡死等回复。
3. **`COMMIT` 必须 `flags=1`**，否则设备不会置 `committed_valid`，后续所有 `PATCH` 都回 `status=4 BAD_STATE`。
4. **整帧 LZ4 用单块**：`BEGIN(raw=2048000)` + 一个 `DATA(flags=1, raw=2048000)` + `COMMIT`。
   裸块路径单块上限是 508 KiB（`520192`），要发整帧必须走 LZ4。

## 4. sidecar（`panel/qdc1.c`）stdin 协议

上位机和 USB 之间由这个 C 程序隔开，它独占设备，因此**任何时候只能有一个进程持有 USB**。

| 首字节 | 后续 | 说明 |
|---|---|---|
| `0x01` | 800×1280×3 B RGB888 | 整帧：转 BGR565 → LZ4 → BEGIN/DATA/COMMIT |
| `0x02` | `u32 n` + n×(`u16 x,u16 y`,1024 B BGR565) | 局部 tile：应用到影子 framebuffer → 算新哈希 → PATCH |
| `0x03` | `u8 cmd` + `u8 arg` | 控屏：`1` 亮度 / `2` 待机 / `3` 唤醒 / `4` 查状态 / `5` 握手 |
| `0x04` | 800×1280×2 B BGR565 | 屏保用：跳过 RGB 转换直接整帧 |

stdout 每条命令一行 JSON（`ready/features/frame/patch/saver/keepalive/lost/brightness/state`），
host 转发给前端做状态面板。

sidecar 负责的三件额外的事：

- **保活**：1.5 秒没有新帧就发 `HELLO`（固件 `QD_HOST_TIMEOUT_MS = 5000`，5 秒没命令会自己画「等待连接」页）
- **链路看门狗**：任何命令 2 秒等不到 ACK（或写失败）→ 判定 USB 链路已死 → `exit(3)`，交给 host 重新枚举
- **patch 链**：本地维护影子 framebuffer，`PATCH` 的 `offset` 必须是设备上一帧的哈希，对不上固件会 `status=4`

## 5. 实测数字（MacBook + CS80）

| 路径 | 数据量 | 耗时 |
|---|---|---|
| 裸整帧（4 块 × 512 KB） | 2 MB | 195–201 ms（USB 有效 ≈ **10.5 MB/s**，与块大小无关） |
| 整帧 LZ4（真实仪表盘） | ~50 KB（42×） | **20 ms** |
| 局部 patch（1 个 32×16 tile） | **64 B** | 1–2 ms |
| 屏保视频 12 fps（ffmpeg 解码 800×1280） | 250 KB / 帧 | 59 ms / 帧 |
| 固件空闲堆 | — | 297 KB |

> 正确性自证：`PATCH` 的基准哈希必须与设备一致，且 `VERIFY` 会回读 framebuffer 的 XXH32。
> 只要 `VERIFY` 与本机整帧哈希一致，就说明屏上像素与主机渲染逐字节相同 —— 没有摄像头也能验收。
