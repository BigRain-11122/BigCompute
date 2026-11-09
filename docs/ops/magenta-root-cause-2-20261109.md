# 洋红占位定因第二轮（真因定谳 + 修复 + 离线仪器）

时间：2026-11-09 19:0x　执行体：BigCompute-OSLoop　任务：T-20261109-7 步③ / 新 T-20261109-10

## 一、上一轮的假说被现网数据推翻

11-09 18:25:10 有一次带 A 路修复（UseBuiltInPipeline + AuditShaderPipeline fail-closed）的真实渲染，
读数如下（media/pool-ignite/B-CITY3D-02/20261109-182443/render-metrics.json）：

    pipeline=builtin  srp_active=0  materials_standard=50  materials_urp=0
    exit=2  render_ok=false  frames=6  blank_frames=1

即：管线确实是内置、材质确实全是 Standard，**洋红仍然在 5/6 帧**（29.07%–65.19%）。
「URP 与内置 shader 失配」这个假说，被自己修好之后的数据推翻了。

## 二、真因：场景里两个渲染器指向一个不存在的材质

逐帧看图（frame_02.png）：**只有地板和天花板是洋红，墙、门、灯板都正常着色**——不是全局 shader 崩溃，
而是特定对象缺材质，Unity 用洋红兜底。

离线静态取证（不开编辑器）：

- 场景 Assets/BakeRoom.unity 中 FloorSlab 与 CeilSlab 的 m_Materials 都是
  `{fileID: 2100000, guid: 31321ba15b8f8eb4c954353edc038b1d, type: 2}`
- 全工程 .meta 按该 guid 检索：**0 命中**（Assets 下除 AD-0* 资源包外没有任何 .mat）
- 即：悬空材质引用（与 GraphicsSettings 里那个 0 命中的 m_CustomRenderPipeline 同族）

为什么上一轮的 51/51 材质普查看不出来：它只走 `AssetDatabase.FindAssets("t:Material")`，
只遍历磁盘上的 .mat，**从不检查场景引用能不能解析**。缺的那份材质根本不在磁盘上，因此既不会被计数、
也不会被判成异常。

## 三、一个根因两个症状

frame_05 被判「空白」（luma_std=0，阈值 2.0）。纯洋红亮度 = 0.2126×255 + 0.0722×255 = **72.6**，
而 frame_05 的 luma_mean 正是 **72.62**、std 恰好 0 —— 俯视机位整屏都是洋红地板，被当成纯色空白。
所以 exit=2 的「空白帧」和洋红是同一个缺陷。

## 四、修法（出生地 + 兜底两层）

1. 工程侧（出生地）：新建 `Assets/RenderSpike/SlabMat.mat`（Standard shader，guid `7f3c1a2b…`），
   把 BakeRoom.unity 里两处悬空引用改写为该 guid（改 2 处）。
2. 渲染侧（兜底）：`City3DBatchRender.cs` 增 `RepairMissingMaterials()`——渲染前遍历全部 Renderer，
   把 null 材质/无 shader 材质替换为内置管线兜底材质，把被修对象名写入指标
   `materials_repaired` / `materials_repaired_objects`；兜底材质也拿不到时**不渲染直接报错**
   （`material_unrepairable`），不再允许产出洋红。
3. 离线仪器：`Tools/scene_material_census.py`（run/selftest），在花掉一次编辑器启动之前回答
   「场景里的材质引用都能解析吗」。

## 五、实跑读数

    修前 VERDICT=SCENE-MATERIAL-UNRESOLVED scenes=6 slots_resolved=3 unresolved=2
         UNRESOLVED Assets/BakeRoom.unity : FloorSlab -> guid 31321ba1…
         UNRESOLVED Assets/BakeRoom.unity : CeilSlab  -> guid 31321ba1…
    修后 VERDICT=SCENE-MATERIAL-CLEAN scenes=6 slots_resolved=5 unresolved=0
    selftest 9/9 PASS（含「悬空 guid 必点名对象」「空槽不算缺陷」「内置零 guid 不算缺陷」
                     「无场景时判 census 无效而不是 CLEAN」「两对象分别点名」「确定性」）

部署复核：入口脚本源件与 labbench 部署件 sha256 一致 `69bf971c94d5efa4`。

## 六、诚实边界（未完成的部分）

- **判据③（复跑渲染出 placeholder_frames=0 且 qc_verdict=FRAME-QC-PASS）本轮未达成**：
  现测 GPU 显存余量 2304MiB < 6144 门槛（他司 krea2 LoRA 训练与 Tuanjie 编辑器在跑），
  按让路律不抢。入口 sha 已变 → 指纹改变，`BigCompute-PoolIgniter` 车道一开即自动重渲。
- C# 改动本轮**未经编译器验证**（未启动编辑器）。若 `RepairMissingMaterials` 有编译错误，
  会以 exit=1 + 日志错误行形式出现，而非静默洋红。
- 材质资产是手写 YAML：若字段与 2022.3.62t15 的序列化格式不完全吻合，Unity 会以缺省值补齐或告警，
  这一点同样要靠下一次真实渲染确认。
- 普查器只覆盖「场景 → 材质」引用；不覆盖 prefab、不覆盖材质 → shader/贴图的二级引用（后者已有
  既有离线普查覆盖 82/82 贴图解析）。