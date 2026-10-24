using System;
using System.IO;
using GameReaderCommon;
using Newtonsoft.Json.Linq;
using SimHub.Plugins;

namespace Iagent
{
    /// <summary>
    /// Exposes what the iagent coach is saying as SimHub properties (CoachPlugin.Cue, .Feedback,
    /// .Summary, .Focus, .Seq) by reading the file `iagent live run` writes.
    /// </summary>
    [PluginDescription("What the iagent coach is saying, for dashboards")]
    [PluginAuthor("iagent")]
    [PluginName("iagent coach")]
    public class CoachPlugin : IPlugin, IDataPlugin
    {
        static readonly string FeedPath = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".iagent", "dash.json");

        string cue = "", feedback = "", summary = "", focus = "";
        int seq;
        DateTime lastCheck = DateTime.MinValue;
        DateTime lastWrite = DateTime.MinValue;

        public PluginManager PluginManager { get; set; }

        public void Init(PluginManager pluginManager)
        {
            this.AttachDelegate("Cue", () => cue);
            this.AttachDelegate("Feedback", () => feedback);
            this.AttachDelegate("Summary", () => summary);
            this.AttachDelegate("Focus", () => focus);
            this.AttachDelegate("Seq", () => seq);
        }

        public void DataUpdate(PluginManager pluginManager, ref GameData data)
        {
            var now = DateTime.UtcNow;
            if ((now - lastCheck).TotalMilliseconds < 100) return;  // called every frame; 10 Hz is plenty
            lastCheck = now;
            try
            {
                var written = File.GetLastWriteTimeUtc(FeedPath);
                if (written == lastWrite) return;
                var feed = JObject.Parse(File.ReadAllText(FeedPath));
                cue = (string)feed["cue"] ?? "";
                feedback = (string)feed["feedback"] ?? "";
                summary = (string)feed["summary"] ?? "";
                focus = (string)feed["focus"] ?? "";
                seq = (int?)feed["seq"] ?? 0;
                lastWrite = written;
            }
            catch (IOException) { }                      // not there yet, or mid-write: try again next time
            catch (Newtonsoft.Json.JsonException) { }
        }

        public void End(PluginManager pluginManager) { }
    }
}
