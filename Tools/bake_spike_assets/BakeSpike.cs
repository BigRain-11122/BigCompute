// City3D 室内烘焙批 spike 批模式入口（BigCompute M25·J1-J4 预注册=R-20260929-city3d-interior-bake-lane §二）
// 用法：Tuanjie.exe -batchmode -projectPath <spike工程> -executeMethod City3DBakeSpike.Entry.Run -logFile <log>
// 环境变量：BAKE_SPIKE_LIGHTMAPPER=ProgressiveGPU|ProgressiveCPU（缺省 CPU）；BAKE_SPIKE_OUT=<metrics.json 绝对路径>
// J1=单样板间烘焙时长；J2=内存/显存峰值记录；J3=AO+光照贴图产物；J4=记账随 run_bake.ps1 tx_id 轨。
// 光基准=City3D 官方光基准档 v1（BC-P-19·正典只读抄录=FluxVerse City3D-staging/official-baseline.md
// AD-022 现代城市段·O-034 B 腿同律）：暖白主光 1.2/(50,212.23,0)/软影0.8+补光0.27/(20,148,0)+Skybox 环境光。
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Profiling;
using UnityEngine.Rendering;
using URP = UnityEngine.Rendering.Universal;

namespace City3DBakeSpike
{
    public static class Entry
    {
        static readonly Dictionary<string, object> M = new Dictionary<string, object>();

        public static void Run()
        {
            try
            {
                M["ts"] = DateTime.Now.ToString("o");
                M["editor"] = Application.unityVersion;
                SetupUrp();
                BuildRoom();
                Bake();
            }
            catch (Exception e)
            {
                M["fatal"] = e.ToString();
            }
            finally
            {
                WriteMetrics();
                int code = M.ContainsKey("fatal") ? 1 : (M.ContainsKey("bake_ok") && (bool)M["bake_ok"] ? 0 : 2);
                EditorApplication.Exit(code);
            }
        }

        // URP 管线资产三 API 链（P3D spike 09-28 实证配方·失败仅记指标不阻断烘焙）
        static void SetupUrp()
        {
            try
            {
                var post = AssetDatabase.LoadAssetAtPath<URP.PostProcessData>(
                    "Packages/com.unity.render-pipelines.universal/Runtime/Data/PostProcessData.asset");
                var rd = ScriptableObject.CreateInstance<URP.UniversalRendererData>();
                if (post != null) rd.postProcessData = post;
                if (!AssetDatabase.IsValidFolder("Assets/URP")) AssetDatabase.CreateFolder("Assets", "URP");
                AssetDatabase.CreateAsset(rd, "Assets/URP/BakeRendererData.asset");
                var urp = URP.UniversalRenderPipelineAsset.Create(rd);
                urp.shadowDistance = 80f;
                AssetDatabase.CreateAsset(urp, "Assets/URP/BakePipeline.asset");
                GraphicsSettings.defaultRenderPipeline = urp;
                M["urp_setup"] = "ok";
            }
            catch (Exception e)
            {
                M["urp_setup"] = "fail:" + e.Message;
            }
        }

        // 室内样板间：AD-021 模块壳围合 + AD-035 灯板光池 + 基元闭合板防漏光
        static void BuildRoom()
        {
            var scene = EditorSceneManager.NewScene(NewSceneSetup.DefaultGameObjects, NewSceneMode.Single);
            int placed = 0;
            placed += PlacePrefab("SM_Bld_House_ExteriorWall_GroundFloor_01", new Vector3(0f, 0f, -5f), 0f);
            placed += PlacePrefab("SM_Bld_House_ExteriorWall_GroundFloor_02", new Vector3(0f, 0f, 5f), 180f);
            placed += PlacePrefab("SM_Bld_House_ExteriorWall_GroundFloor_03", new Vector3(-5f, 0f, 0f), 90f);
            placed += PlacePrefab("SM_Bld_House_ExteriorWall_GroundFloor_Corner_01", new Vector3(5f, 0f, 0f), 270f);
            placed += PlacePrefab("SM_Bld_House_InteriorWall", new Vector3(0f, 0f, 0f), 0f);
            placed += PlacePrefab("SM_Bld_House_Stairs", new Vector3(2.5f, 0f, 2.5f), 0f);
            placed += PlacePrefab("SM_Bld_House_Door_01", new Vector3(-2.5f, 0f, -4.8f), 0f);
            placed += MakeSlab("FloorSlab", new Vector3(0f, -0.1f, 0f), new Vector3(14f, 0.2f, 14f));
            placed += MakeSlab("CeilSlab", new Vector3(0f, 3.6f, 0f), new Vector3(14f, 0.2f, 14f));
            placed += PlacePrefab("Ceiling_Panel_Light", new Vector3(-1.5f, 3.4f, -1.5f), 0f);
            placed += PlacePrefab("Ceiling_Panel_Light", new Vector3(1.5f, 3.4f, 1.5f), 180f);

            var ptGo = new GameObject("BakePointLight");
            var pl = ptGo.AddComponent<Light>();
            pl.type = LightType.Point;
            pl.intensity = 2f;
            pl.range = 10f;
            pl.shadows = LightShadows.Soft;
            ptGo.transform.position = new Vector3(0f, 2.6f, 0f);

            // City3D 官方光基准档直配（BC-P-19·正典只读抄录同上）：室内灯板点光维持+三面对齐
            var sun = GameObject.Find("Directional Light");
            if (sun != null)
            {
                var dl = sun.GetComponent<Light>();
                if (dl != null)
                {
                    dl.intensity = 1.2f; // 暖白主光 1.2
                    dl.color = new Color32(0xFF, 0xF4, 0xD6, 0xFF); // #FFF4D6
                    dl.transform.rotation = Quaternion.Euler(50f, 212.23f, 0f); // 仰50°/方212°
                    dl.shadows = LightShadows.Soft;
                    dl.shadowStrength = 0.8f; // 软影 0.8
                }
            }

            var fillGo = new GameObject("BakeFillLight"); // 补光=主光对侧交叉光·无影
            var fl = fillGo.AddComponent<Light>();
            fl.type = LightType.Directional;
            fl.intensity = 0.27f;
            fl.color = new Color32(0xCC, 0xDD, 0xFF, 0xFF); // #CCDDFF
            fl.shadows = LightShadows.None;
            fillGo.transform.rotation = Quaternion.Euler(20f, 148f, 0f);

            RenderSettings.ambientMode = AmbientMode.Skybox; // Skybox 环境光
            var skyShader = Shader.Find("Skybox/Procedural"); // B2 判例：GetBuiltinExtraResource 不可用→Shader.Find 构材
            if (skyShader != null) RenderSettings.skybox = new Material(skyShader);
            RenderSettings.ambientSkyColor = new Color32(0x36, 0x3A, 0x42, 0xFF);
            RenderSettings.ambientEquatorColor = new Color32(0x1D, 0x20, 0x22, 0xFF);
            RenderSettings.ambientGroundColor = new Color32(0x0C, 0x0B, 0x09, 0xFF);
            RenderSettings.ambientIntensity = 1f;
            RenderSettings.fog = false;
            M["light_baseline"] = "city3d_official_ad022_v1";
            M["objects_placed"] = placed;
            M["scene_saved"] = EditorSceneManager.SaveScene(scene, "Assets/BakeRoom.unity");
        }

        static int PlacePrefab(string nameContains, Vector3 pos, float yRot)
        {
            var guids = AssetDatabase.FindAssets(nameContains + " t:Prefab");
            if (guids.Length == 0) return 0;
            var path = AssetDatabase.GUIDToAssetPath(guids[0]);
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            if (prefab == null) return 0;
            var go = (GameObject)PrefabUtility.InstantiatePrefab(prefab);
            if (go == null) return 0;
            go.transform.position = pos;
            go.transform.rotation = Quaternion.Euler(0f, yRot, 0f);
            MarkStatic(go);
            return 1;
        }

        static int MakeSlab(string name, Vector3 pos, Vector3 scale)
        {
            var go = GameObject.CreatePrimitive(PrimitiveType.Cube);
            go.name = name;
            go.transform.position = pos;
            go.transform.localScale = scale;
            MarkStatic(go);
            return 1;
        }

        static void MarkStatic(GameObject go)
        {
            GameObjectUtility.SetStaticEditorFlags(go,
                StaticEditorFlags.ContributeGI | StaticEditorFlags.OccluderStatic | StaticEditorFlags.OccludeeStatic);
        }

        static void Bake()
        {
            bool useGpu = string.Equals(Environment.GetEnvironmentVariable("BAKE_SPIKE_LIGHTMAPPER"),
                "ProgressiveGPU", StringComparison.OrdinalIgnoreCase);
            var s = new LightingSettings();
            s.lightmapper = useGpu ? LightingSettings.Lightmapper.ProgressiveGPU : LightingSettings.Lightmapper.ProgressiveCPU;
            s.aoEnabled = true; // J3：AO 随烘焙
            Lightmapping.lightingSettings = s;
            Lightmapping.bakedGI = true;

            long memBefore = Profiler.GetTotalAllocatedMemoryLong();
            var sw = Stopwatch.StartNew();
            bool ok = Lightmapping.Bake(); // 批模式同步烘焙
            sw.Stop();

            M["lightmapper"] = useGpu ? "ProgressiveGPU" : "ProgressiveCPU";
            M["bake_ok"] = ok;
            M["bake_seconds"] = sw.Elapsed.TotalSeconds; // J1
            M["mem_before_bytes"] = memBefore;
            M["mem_after_bytes"] = Profiler.GetTotalAllocatedMemoryLong(); // J2 内部面（VRAM 峰值=run_bake.ps1 外部采样）

            AssetDatabase.SaveAssets();
            AssetDatabase.Refresh();
            var exrs = Directory.GetFiles(Application.dataPath, "Lightmap-*.exr", SearchOption.AllDirectories);
            long total = 0;
            foreach (var f in exrs) total += new FileInfo(f).Length;
            M["lightmap_files"] = exrs.Length; // J3 产物面
            M["lightmap_bytes"] = total;
            M["runtime_lightmaps"] = LightmapSettings.lightmaps.Length;
        }

        static void WriteMetrics()
        {
            var path = Environment.GetEnvironmentVariable("BAKE_SPIKE_OUT");
            if (string.IsNullOrEmpty(path)) path = "bake-spike-metrics.json";
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(path)));
            var sb = new StringBuilder("{");
            bool first = true;
            foreach (var kv in M)
            {
                if (!first) sb.Append(",");
                first = false;
                sb.Append("\"").Append(kv.Key).Append("\":");
                object v = kv.Value;
                if (v is string str) sb.Append("\"").Append(str.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\n", " ")).Append("\"");
                else if (v is bool b) sb.Append(b ? "true" : "false");
                else if (v is double d) sb.Append(d.ToString("0.###", CultureInfo.InvariantCulture));
                else sb.Append(Convert.ToString(v, CultureInfo.InvariantCulture));
            }
            sb.Append("}");
            File.WriteAllText(path, sb.ToString());
            UnityEngine.Debug.Log("BakeSpike metrics written: " + path);
        }
    }
}
