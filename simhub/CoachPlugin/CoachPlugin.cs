using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Linq;
using GameReaderCommon;
using Newtonsoft.Json.Linq;
using SimHub.Plugins;

namespace Iagent
{
    /// <summary>
    /// Shows the iagent coach on a dashboard: reads the feed `iagent live run` (or the browser's
    /// Coaching tab) writes to ~/.iagent/dash.json, exposes the coming corner and the cue as
    /// properties (CoachPlugin.Corner, .CornerName, .Action, .Hard, .Gear, .Direction, .ToGoM,
    /// .Cue, .Focus, .Seq), and draws the pedal trace and the lap strip as images
    /// (CoachPlugin.TraceImage, .StripImage: paths for an "Image from file" item).
    /// </summary>
    [PluginDescription("The iagent coach's corner, cue and pedal trace, for dashboards")]
    [PluginAuthor("iagent")]
    [PluginName("iagent coach")]
    public class CoachPlugin : IPlugin, IDataPlugin
    {
        static readonly string Folder = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".iagent");
        static readonly string FeedPath = Path.Combine(Folder, "dash.json");

        // Sizes from simhub/dash-design.html.
        const int TraceW = 776, TraceH = 84, TraceTop = 22, TraceBottom = 8;
        const int StripW = 776, StripH = 12;

        static readonly Color Red = Color.FromArgb(255, 59, 48), Green = Color.FromArgb(25, 195, 125),
            Amber = Color.FromArgb(255, 194, 51), Grey = Color.FromArgb(58, 58, 66),
            Purple = Color.FromArgb(138, 92, 217), Plot = Color.FromArgb(11, 11, 14);

        int corner, gear, seq;
        string cornerName = "", action = "", direction = "", cue = "", focus = "";
        bool hard;
        double targetM = -1, lengthM;
        int toGoM;
        string traceImage = "", stripImage = "";
        DateTime lastCheck = DateTime.MinValue, lastWrite = DateTime.MinValue;

        public PluginManager PluginManager { get; set; }

        public void Init(PluginManager pluginManager)
        {
            this.AttachDelegate("Corner", () => corner);
            this.AttachDelegate("CornerName", () => cornerName);
            this.AttachDelegate("Action", () => action);
            this.AttachDelegate("Hard", () => hard);
            this.AttachDelegate("Gear", () => gear > 0 ? "G" + gear : "");
            this.AttachDelegate("Direction", () => direction);
            this.AttachDelegate("ToGoM", () => toGoM);
            this.AttachDelegate("Cue", () => cue);
            this.AttachDelegate("Focus", () => focus);
            this.AttachDelegate("Seq", () => seq);
            this.AttachDelegate("TraceImage", () => traceImage);
            this.AttachDelegate("StripImage", () => stripImage);
        }

        public void DataUpdate(PluginManager pluginManager, ref GameData data)
        {
            if (data.GameRunning && data.NewData != null && targetM >= 0 && lengthM > 0)
            {
                var pos = data.NewData.TrackPositionPercent;
                if (pos > 1) pos /= 100;  // in case it's a percentage
                toGoM = (int)Math.Round(((targetM - pos * lengthM) % lengthM + lengthM) % lengthM);
            }

            var now = DateTime.UtcNow;
            if ((now - lastCheck).TotalMilliseconds < 100) return;  // called every frame; 10 Hz is plenty
            lastCheck = now;
            try
            {
                var written = File.GetLastWriteTimeUtc(FeedPath);
                if (written == lastWrite) return;
                Read(JObject.Parse(File.ReadAllText(FeedPath)));
                lastWrite = written;
            }
            catch (IOException) { }                      // not there yet, or mid-write: try again next time
            catch (Newtonsoft.Json.JsonException) { }
            catch (System.Runtime.InteropServices.ExternalException) { }  // an image in use: next time
        }

        public void End(PluginManager pluginManager) { }

        void Read(JObject feed)
        {
            seq = (int?)feed["seq"] ?? 0;
            lengthM = (double?)feed["length_m"] ?? 0;
            cue = (string)feed["cue"] ?? "";
            focus = (string)feed["focus"] ?? "";
            var c = feed["corner"] as JObject;
            corner = (int?)c?["id"] ?? 0;
            cornerName = (string)c?["name"] ?? "";
            action = (string)c?["action"] ?? "";
            hard = (bool?)c?["hard"] ?? false;
            gear = (int?)c?["gear"] ?? 0;
            direction = (string)c?["direction"] ?? "";
            targetM = (double?)c?["target_m"] ?? -1;
            // Two files in turn, so a dashboard that caches an image by its path sees the new one.
            var side = seq % 2;
            traceImage = Save(DrawTrace(feed["trace"] as JObject), "trace-" + side + ".png");
            stripImage = Save(DrawStrip(feed["strip"] as JObject), "strip-" + side + ".png");
        }

        static string Save(Bitmap image, string name)
        {
            var path = Path.Combine(Folder, name);
            using (image) image.Save(path, ImageFormat.Png);
            return path;
        }

        // --- the pedal trace: last lap as solid lines, the reference as a soft band with a dotted
        // centre line, the brake point arrow, the apex ---------------------------------------------

        static Bitmap DrawTrace(JObject t)
        {
            var image = new Bitmap(TraceW, TraceH);
            using (var g = Graphics.FromImage(image))
            {
                g.SmoothingMode = SmoothingMode.AntiAlias;
                g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.AntiAliasGridFit;
                g.Clear(Color.Black);
                using (var bg = new SolidBrush(Plot)) g.FillPath(bg, Rounded(new RectangleF(0, 0, TraceW, TraceH), 8));
                using (var baseline = new Pen(Color.FromArgb(38, 38, 44), 2) { StartCap = LineCap.Round, EndCap = LineCap.Round })
                    g.DrawLine(baseline, 8, Y(0), TraceW - 8, Y(0));
                if (t == null) return image;

                var apex = (float?)t["apex"];
                if (apex != null)
                {
                    var x = apex.Value * TraceW;
                    using (var dashed = new Pen(Color.FromArgb(102, 102, 102), 1) { DashPattern = new[] { 4f, 4f } })
                        g.DrawLine(dashed, x, TraceTop, x, Y(0));
                    using (var font = new Font(FontName(), 9f, FontStyle.Regular, GraphicsUnit.Point))
                    using (var grey = new SolidBrush(Color.FromArgb(136, 136, 136)))
                    using (var centred = new StringFormat { Alignment = StringAlignment.Center })
                        g.DrawString("APEX", font, grey, x, 1, centred);
                }

                foreach (var (key, colour) in new[] { ("target_throttle", Green), ("target_brake", Red) })
                    using (var band = RoundPen(Color.FromArgb(89, colour), 12))
                        DrawPedal(g, band, Values(t[key]));
                foreach (var (key, colour) in new[] { ("target_throttle", Color.FromArgb(143, 240, 194)), ("target_brake", Color.FromArgb(255, 157, 151)) })
                    using (var dots = RoundPen(colour, 3))
                    {
                        dots.DashCap = DashCap.Round;
                        dots.DashPattern = new[] { 0.01f, 7f / 3f };  // in pen widths: a dot every 7 px
                        DrawPedal(g, dots, Values(t[key]));
                    }
                foreach (var (key, colour) in new[] { ("last_throttle", Green), ("last_brake", Red) })
                    using (var line = RoundPen(colour, 3.5f))
                        DrawPedal(g, line, Values(t[key]));

                if (t["arrow"] is JArray arrow && arrow.Count == 2)
                {
                    float from = (float)arrow[0] * TraceW, to = (float)arrow[1] * TraceW, y = 8;
                    var dir = to > from ? 1 : -1;
                    using (var pen = RoundPen(Amber, 3))
                    {
                        g.DrawLine(pen, from, y, to - dir * 2, y);
                        g.DrawLines(pen, new[] { new PointF(to - dir * 8, y - 5), new PointF(to - dir, y), new PointF(to - dir * 8, y + 5) });
                        g.DrawLine(pen, from, y - 4, from, y + 4);
                    }
                }
            }
            return image;
        }

        static float Y(float v) => TraceH - TraceBottom - v * (TraceH - TraceTop - TraceBottom);

        /// <summary>A pedal trace, left out wherever the pedal is at rest so there's no line along the bottom.</summary>
        static void DrawPedal(Graphics g, Pen pen, float[] v)
        {
            if (v == null || v.Length < 2) return;
            var run = new System.Collections.Generic.List<PointF>();
            for (var i = 0; i <= v.Length; i++)
            {
                if (i < v.Length && v[i] > 0.01f)
                {
                    run.Add(new PointF(i * (float)TraceW / (v.Length - 1), Y(v[i])));
                    continue;
                }
                if (run.Count > 1) g.DrawLines(pen, run.ToArray());
                run.Clear();
            }
        }

        // --- the lap strip: each corner's share of the lap coloured by time gained or lost this lap,
        // the next corner, the focus (the car's position is a dashboard item over it) -------------

        static Bitmap DrawStrip(JObject s)
        {
            var image = new Bitmap(StripW, StripH);
            using (var g = Graphics.FromImage(image))
            {
                g.SmoothingMode = SmoothingMode.AntiAlias;
                g.Clear(Color.Black);
                if (s == null) return image;
                foreach (var sector in s["sectors"] ?? new JArray())
                {
                    float x0 = (float)sector["from"] * StripW + 1, x1 = (float)sector["to"] * StripW - 1;
                    var colour = (string)sector["state"] == "gain" ? Green
                        : (string)sector["state"] == "small" ? Amber
                        : (string)sector["state"] == "loss" ? Red : Grey;
                    if (x1 > x0)
                        using (var fill = new SolidBrush(colour)) g.FillPath(fill, Rounded(new RectangleF(x0, 0, x1 - x0, StripH), 3));
                }
                foreach (var span in s["focus"] ?? new JArray())
                {
                    float x0 = (float)span[0] * StripW + 1, x1 = (float)span[1] * StripW - 1;
                    using (var pen = new Pen(Purple, 2)) g.DrawPath(pen, Rounded(new RectangleF(x0, 1, x1 - x0, StripH - 2), 3));
                }
                var next = (float?)s["next"];
                if (next != null)
                    using (var white = new SolidBrush(Color.White))
                        g.FillPath(white, Rounded(new RectangleF(next.Value * StripW - 2, 0, 4, StripH), 2));
            }
            return image;
        }

        // --- helpers -----------------------------------------------------------------------------

        static float[] Values(JToken token) => (token as JArray)?.Select(v => (float)v).ToArray();

        static Pen RoundPen(Color colour, float width) => new Pen(colour, width)
        {
            StartCap = LineCap.Round, EndCap = LineCap.Round, LineJoin = LineJoin.Round,
        };

        static GraphicsPath Rounded(RectangleF r, float radius)
        {
            var d = Math.Min(radius * 2, Math.Min(r.Width, r.Height));
            var path = new GraphicsPath();
            path.AddArc(r.X, r.Y, d, d, 180, 90);
            path.AddArc(r.Right - d, r.Y, d, d, 270, 90);
            path.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
            path.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
            path.CloseFigure();
            return path;
        }

        static string FontName() =>
            System.Drawing.FontFamily.Families.Any(f => f.Name == "Bahnschrift") ? "Bahnschrift" : "Segoe UI";
    }
}
