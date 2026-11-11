# 判据帧洋红·出生地根治（B-CITY3D-02 / 2026-11-12）

> 一句话：前两次修复改的是**产物**（场景文件引用、渲染侧守卫），而场景每次烘焙都会被
> 生成器重写，所以修复活不过下一次烘焙。本轮把修复挪到**生成器**，并补上一条看不见
> SRP 材质的守卫判据。判据③（六帧洋红消失）仍需 GPU 开窗实跑，未验不报。

## 一、修复前的三份硬证据

| # | 证据 | 读数 |
|---|---|---|
| 1 | 渲染回执 11-12 04:58:53（`media/pool-ignite/B-CITY3D-02/20261112-045450`） | `placeholder_frames=6/6`、`qc=FRAME-QC-INSUFFICIENT-PLACEHOLDER`、`magenta_max=100.0%`、`pipeline=builtin`、`material_slots=14`、`materials_repaired=0` |
| 2 | 部署工程场景材质普查（修前） | `SCENE-MATERIAL-UNRESOLVED scenes=6 slots_resolved=3 unresolved=2`，两条都是 `BakeRoom.unity :: FloorSlab / CeilSlab -> guid 31321ba1…`；场景 mtime 11-12 01:42 |
| 3 | guid 真身（本轮新证） | `Library/PackageCache/com.unity.render-pipelines.universal@14.2.0-t1/Runtime/Materials/Lit.mat.meta = 31321ba15b8f8eb4c954353edc038b1d` |

结论：**不是悬空引用**，而是 URP 包内 Lit 材质。引用非 null、shader 也非 null，
所以旧守卫 `mats[i] != null && mats[i].shader != null` 必然放行；但渲染卡把管线强制
清成 builtin，URP Lit 在 builtin 下画不出来 → 洋红。这也解释了 `material_slots=14`
却 `repaired=0`。

## 二、成因链（三环齐全才复发）

1. `BuildRoom()` 每次 NewScene + `SaveScene("Assets/BakeRoom.unity")` → 直接改场景的修复是耗材。
2. `CreatePrimitive` 在本工程（`SetupUrp()` 已把默认管线设为 URP）给出的默认材质 = URP Lit
   → 两块板子天生带 SRP 材质。
3. 渲染卡强制 builtin（`srp_active=0`）→ SRP 材质失效 → 洋红兜底。

## 三、改动（两处，都在出生地；仓内源件与 labbench 部署件 sha 逐件相等）

1. `Tools/bake_spike_assets/BakeSpike.cs`
   - 新增 `SlabMaterial()`：优先加载 `Assets/RenderSpike/SlabMat.mat`（Standard），缺失则创建并保存；
     拿不到内置 shader 即抛错，不静默降级。
   - `MakeSlab()` 显式 `mr.sharedMaterial = SlabMaterial()`，并把 `slab_material` /
     `slab_material_applied_<name>` 写进烘焙指标。
2. `Tools/batch_render/City3DBatchRender.cs`
   - 新增 `BrokenReason(Material)`：`null-material` / `null-shader` / `empty-shader-name` /
     `error-shader`（`Hidden/InternalErrorShader`）/ `srp-shader-under-builtin`（URP、HDRP 前缀）。
   - `RepairMissingMaterials` 改用该判据，并新增 `materials_repaired_reasons` 逐槽原因串。

sha256（前 16）：`BakeSpike.cs 9047EF6F51486E4A`、`City3DBatchRender.cs CE1DC80818FB2201`，两侧相同。

## 四、验证据（本轮真跑）

- 烘焙卡真跑（CPU 车道开窗，06:37:42 起，248.3s，`ok=true`）：
  `slab_material=loaded:Assets/RenderSpike/SlabMat.mat·Standard`，
  `slab_material_applied_FloorSlab/CeilSlab=Standard`，
  `bake_ok=true`、`lightmap_files=2`、`lightmap_bytes=12,744,683`，图集 QC `LIGHTMAP-QC-PASS`（min_lit=100.0%）。
- 烘焙后场景被**重新生成**：L790/L901 = guid `7f3c1a2b…`（SlabMat），`31321ba1…` 零命中。
- 材质普查（修后）：`SCENE-MATERIAL-CLEAN scenes=6 slots_resolved=5 unresolved=0`。
- 对照：修前 3/2 → 修后 5/0；关键在于「重生成之后仍然干净」，这才是本次与 11-09 那次的分别。

## 五、诚实边界

1. 判据③（六帧洋红消失）**未验**：渲染卡 `lane=gpu`，06:2x 实测 `vram_free=3298MiB < 6144`，按让路律不抢。
   `.cs` 内容已变 → payload 指纹变化 → 重复守卫不会跳过，GPU 一开窗守候器即自动重渲。
2. `BakeSpike.cs` 已由本次烘焙真跑证明可编译可运行；`City3DBatchRender.cs` 的编译**尚未被任何一次运行验证**。
3. `scene_material_census` 只扫 `Assets/`，把该 guid 判成 `UNRESOLVED` 属口径正确、结论偏窄：
   它无法区分「悬空」与「包内材质」。工具侧分类改进入队（队列 M199）。

## 六、证据指针

- `state/bake-spike-magenta-birthsite-20261112.txt`（本轮原始取证）
- `state/scene-material-census-deployed-20261112.json`（修前）/ `…-post.json`（修后）
- `state/pool-ignitions.jsonl` 06:39 那条 B-CITY3D-01 回执（含 `slab_material*` 三字段）
