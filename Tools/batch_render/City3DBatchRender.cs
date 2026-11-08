// City3D 判据帧离线批渲染 spike 入口（B-CITY3D-02 · O-20260929-007「3D 离线渲染 ≥2」第二卡 · BigCompute T-20261108-4）
// 用法：Tuanjie.exe -batchmode -projectPath <工程> -executeMethod City3DBatchRender.Entry.Run -logFile <log>
// 环境变量：RENDER_OUT=<metrics.json 绝对路径>；RENDER_DIR=<png 输出目录>；
//           RENDER_W/RENDER_H（缺省 1280x720）；RENDER_VIEWS（缺省 6，上限 6 个内置机位）
// 判据：①帧数达标 ②每帧非空白（亮度标准差 > 2.0/255）③帧间不重复（sha256 互不相同）→ render_ok
// 说明：判据帧=固定六机位（四向平视 + 俯视 + 斜俯），与场景内容无关，可跨工程复用做验收面比对。
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using URP = UnityEngine.Rendering.Universal;

namespace City3DBatchRender
{
    public static class Entry
    {
        const float STD_THRESHOLD = 2.0f; // 亮度标准差下限（0-255 标度）；纯色/全黑/全白一律判空白
        static readonly Dictionary<string, object> M = new Dictionary<string, object>();

        public static void Run()
        {
            try
            {
                M["ts"] = DateTime.Now.ToString("o");
                M["editor"] = Application.unityVersion;
                SetupUrp();
                LoadScene();
                RenderFrames();
            }
            catch (Exception e)
            {
                M["fatal"] = e.ToString();
            }
            finally
            {
                WriteMetrics();
                int code = M.ContainsKey("fatal") ? 1
                    : (M.ContainsKey("render_ok") && (bool)M["render_ok"] ? 0 : 2);
                EditorApplication.Exit(code);
            }
        }

        static void SetupUrp()
        {
            try
            {
                var post = AssetDatabase.LoadAssetAtPath<URP.PostProcessData>(
                    "Packages/com.unity.render-pipelines.universal/Runtime/Data/PostProcessData.asset");
                var rd = ScriptableObject.CreateInstance<URP.UniversalRendererData>();
                if (post != null) rd.postProcessData = post;
                if (!AssetDatabase.IsValidFolder("Assets/URP")) AssetDatabase.CreateFolder("Assets", "URP");
                if (!File.Exists("Assets/URP/RenderSpikeRendererData.asset"))
                    AssetDatabase.CreateAsset(rd, "Assets/URP/RenderSpikeRendererData.asset");
                var saved = AssetDatabase.LoadAssetAtPath<URP.UniversalRendererData>(
                    "Assets/URP/RenderSpikeRendererData.asset");
                var urp = URP.UniversalRenderPipelineAsset.Create(saved != null ? (URP.ScriptableRendererData)saved : rd);
                urp.shadowDistance = 80f;
                if (!File.Exists("Assets/URP/RenderSpikePipeline.asset"))
                    AssetDatabase.CreateAsset(urp, "Assets/URP/RenderSpikePipeline.asset");
                var pipe = AssetDatabase.LoadAssetAtPath<URP.UniversalRenderPipelineAsset>(
                    "Assets/URP/RenderSpikePipeline.asset");
                GraphicsSettings.defaultRenderPipeline = pipe != null ? pipe : urp;
                M["urp_setup"] = "ok";
            }
            catch (Exception e)
            {
                M["urp_setup"] = "fail:" + e.Message;
            }
        }

        // 已有烘焙样板间则直接复用（同工程同场景=验收面可比）；缺失则构造最小可渲染场景
        static void LoadScene()
        {
            const string baked = "Assets/BakeRoom.unity";
            if (File.Exists(baked))
            {
                EditorSceneManager.OpenScene(baked, OpenSceneMode.Single);
                M["scene"] = "reused:" + baked;
            }
            else
            {
                EditorSceneManager.NewScene(NewSceneSetup.DefaultGameObjects, NewSceneMode.Single);
                var floor = GameObject.CreatePrimitive(PrimitiveType.Cube);
                floor.name = "RenderFloor";
                floor.transform.position = new Vector3(0f, -0.1f, 0f);
                floor.transform.localScale = new Vector3(14f, 0.2f, 14f);
                for (int i = 0; i < 4; i++)
                {
                    var b = GameObject.CreatePrimitive(PrimitiveType.Cube);
                    b.name = "RenderBlock_" + i;
                    float a = i * Mathf.PI * 0.5f;
                    b.transform.position = new Vector3(Mathf.Cos(a) * 2.5f, 0.9f, Mathf.Sin(a) * 2.5f);
                    b.transform.localScale = new Vector3(1.2f, 1.8f, 1.2f);
                }
                var key = GameObject.Find("Directional Light");
                if (key != null)
                {
                    var dl = key.GetComponent<Light>();
                    if (dl != null)
                    {
                        dl.intensity = 1.2f;
                        dl.color = new Color32(0xFF, 0xF4, 0xD6, 0xFF);
                        dl.transform.rotation = Quaternion.Euler(50f, 212.23f, 0f);
                    }
                }
                RenderSettings.ambientMode = AmbientMode.Skybox;
                var skyShader = Shader.Find("Skybox/Procedural");
                if (skyShader != null) RenderSettings.skybox = new Material(skyShader);
                M["scene"] = "built:minimal";
            }
            M["scene_objects"] = UnityEngine.Object.FindObjectsOfType<GameObject>().Length;
        }

        struct View
        {
            public Vector3 eye;
            public Vector3 target;
            public View(float x, float y, float z, float tx, float ty, float tz)
            {
                eye = new Vector3(x, y, z);
                target = new Vector3(tx, ty, tz);
            }
        }

        static readonly View[] VIEWS =
        {
            new View(0f, 1.7f, -6f, 0f, 1.5f, 0f),
            new View(6f, 1.7f, 0f, 0f, 1.5f, 0f),
            new View(0f, 1.7f, 6f, 0f, 1.5f, 0f),
            new View(-6f, 1.7f, 0f, 0f, 1.5f, 0f),
            new View(0f, 3.2f, 0.1f, 0f, 0f, 0f),
            new View(3.2f, 2.4f, 3.2f, 0f, 0.6f, 0f),
        };

        static void RenderFrames()
        {
            int w = EnvInt("RENDER_W", 1280);
            int h = EnvInt("RENDER_H", 720);
            int views = Mathf.Clamp(EnvInt("RENDER_VIEWS", VIEWS.Length), 1, VIEWS.Length);
            string outDir = Environment.GetEnvironmentVariable("RENDER_DIR");
            if (string.IsNullOrEmpty(outDir)) outDir = "render-spike-frames";
            Directory.CreateDirectory(outDir);

            var camGo = new GameObject("RenderSpikeCamera");
            var cam = camGo.AddComponent<Camera>();
            cam.clearFlags = CameraClearFlags.SolidColor;
            cam.backgroundColor = new Color(0.05f, 0.05f, 0.06f, 1f);
            cam.fieldOfView = 55f;
            cam.nearClipPlane = 0.05f;
            cam.farClipPlane = 500f;

            var rt = new RenderTexture(w, h, 24, RenderTextureFormat.ARGB32);
            var tex = new Texture2D(w, h, TextureFormat.RGB24, false);
            var frames = new List<object>();
            var shas = new HashSet<string>();
            int blank = 0, dup = 0;
            var sw = Stopwatch.StartNew();

            for (int i = 0; i < views; i++)
            {
                var v = VIEWS[i];
                camGo.transform.position = v.eye;
                camGo.transform.LookAt(v.target);
                cam.targetTexture = rt;
                cam.Render();
                RenderTexture.active = rt;
                tex.ReadPixels(new Rect(0, 0, w, h), 0, 0);
                tex.Apply();
                RenderTexture.active = null;

                byte[] png = tex.EncodeToPNG();
                string sha = Sha256(png);
                bool isDup = !shas.Add(sha);
                if (isDup) dup++;

                float std;
                float mean = Luma(tex.GetPixels(), out std);
                bool isBlank = std < STD_THRESHOLD;
                if (isBlank) blank++;

                string name = string.Format(CultureInfo.InvariantCulture, "frame_{0:00}.png", i + 1);
                string path = Path.Combine(outDir, name);
                File.WriteAllBytes(path, png);
                frames.Add(Mini(new Dictionary<string, object>
                {
                    { "index", i + 1 }, { "file", name }, { "bytes", png.Length },
                    { "sha256", sha }, { "luma_mean", Math.Round(mean, 2) },
                    { "luma_std", Math.Round(std, 2) }, { "blank", isBlank }, { "duplicate", isDup },
                    { "eye", string.Format(CultureInfo.InvariantCulture, "{0:0.##},{1:0.##},{2:0.##}", v.eye.x, v.eye.y, v.eye.z) },
                }));
            }
            sw.Stop();
            cam.targetTexture = null;
            UnityEngine.Object.DestroyImmediate(camGo);
            rt.Release();

            M["width"] = w;
            M["height"] = h;
            M["views"] = views;
            M["frame_count"] = frames.Count;
            M["frames"] = frames;
            M["blank_frames"] = blank;
            M["duplicate_frames"] = dup;
            M["render_seconds"] = Math.Round(sw.Elapsed.TotalSeconds, 2);
            M["std_threshold"] = STD_THRESHOLD;
            M["out_dir"] = outDir;
            M["render_ok"] = frames.Count == views && blank == 0 && dup == 0;
        }

        static string Mini(Dictionary<string, object> d)
        {
            var sb = new StringBuilder("{");
            bool first = true;
            foreach (var kv in d)
            {
                if (!first) sb.Append(",");
                first = false;
                sb.Append("\"").Append(kv.Key).Append("\":");
                object v = kv.Value;
                if (v is string s) sb.Append("\"").Append(Esc(s)).Append("\"");
                else if (v is bool b) sb.Append(b ? "true" : "false");
                else sb.Append(Convert.ToString(v, CultureInfo.InvariantCulture));
            }
            sb.Append("}");
            return sb.ToString();
        }

        static float Luma(Color[] px, out float std)
        {
            double sum = 0, sum2 = 0;
            int n = px.Length;
            for (int i = 0; i < n; i++)
            {
                float y = 0.2126f * px[i].r + 0.7152f * px[i].g + 0.0722f * px[i].b;
                sum += y;
                sum2 += (double)y * y;
            }
            double mean = n > 0 ? sum / n : 0;
            double var = n > 0 ? (sum2 / n) - (mean * mean) : 0;
            std = (float)Math.Sqrt(Math.Max(0, var)) * 255f;
            return (float)mean * 255f;
        }

        static string Sha256(byte[] data)
        {
            using (var sha = SHA256.Create())
            {
                byte[] d = sha.ComputeHash(data);
                var sb = new StringBuilder(d.Length * 2);
                foreach (byte b in d) sb.Append(b.ToString("x2", CultureInfo.InvariantCulture));
                return sb.ToString();
            }
        }

        static int EnvInt(string key, int fallback)
        {
            string raw = Environment.GetEnvironmentVariable(key);
            int v;
            return int.TryParse(raw, NumberStyles.Integer, CultureInfo.InvariantCulture, out v) ? v : fallback;
        }

        static string Esc(string s)
        {
            return s.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\n", " ");
        }

        static void WriteMetrics()
        {
            ResolveSceneFileOnce();
            var sb = new StringBuilder("{");
            bool first = true;
            foreach (var kv in M)
            {
                if (!first) sb.Append(",");
                first = false;
                sb.Append("\"").Append(kv.Key).Append("\":");
                object v = kv.Value;
                if (v is string s) sb.Append("\"").Append(Esc(s)).Append("\"");
                else if (v is bool b) sb.Append(b ? "true" : "false");
                else if (v is List<object> list)
                {
                    sb.Append("[");
                    for (int i = 0; i < list.Count; i++)
                    {
                        if (i > 0) sb.Append(",");
                        sb.Append(list[i] as string ?? "null");
                    }
                    sb.Append("]");
                }
                else sb.Append(Convert.ToString(v, CultureInfo.InvariantCulture));
            }
            sb.Append("}");
            string path = Environment.GetEnvironmentVariable("RENDER_OUT");
            if (string.IsNullOrEmpty(path)) path = "render-spike-metrics.json";
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(path)));
            File.WriteAllText(path, sb.ToString());
            UnityEngine.Debug.Log("BatchRender metrics written: " + path);
        }

        // 场景文件留档=验收面可比（只写工程内路径，不动源仓）
        static void ResolveSceneFileOnce()
        {
            try
            {
                var active = EditorSceneManager.GetActiveScene();
                if (!string.IsNullOrEmpty(active.path)) M["scene_file"] = active.path;
            }
            catch (Exception)
            {
                // 场景信息缺失不影响帧面判定
            }
        }
    }
}