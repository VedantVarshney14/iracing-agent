"""`iagent live`: the in-session coach."""

from pathlib import Path

import click

from iagent.cli.base import Ctx
from iagent.telemetry.ibt import IbtSource


@click.group()
def live():
    """The in-session coach: corner cues and feedback spoken while you drive."""


@live.command("run")
@click.option("--replay", "replay", type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="Replay an .ibt recording in real time instead of reading iRacing live.")
@click.option("--speed", default=1.0, show_default=True, help="Replay speed (× real time).")
@click.option("--start", "start_at", type=float, help="Replay from this session time (s).")
@click.option("--voice", default="alba", show_default=True, help="Pocket TTS voice, or a .wav to clone.")
@click.option("--print", "print_only", is_flag=True, help="Print what would be said instead of speaking.")
@click.option("--ref", "ref_id", help="Lap to follow (default: the saved cue plan's).")
@click.option("--learning-laps", default=2, show_default=True, help="Laps with every corner cued.")
@click.option("--threads", default=2, show_default=True, help="CPU threads for speech.")
@click.option("--crewchief", type=click.Choice(["auto", "on", "off"]), default="auto", show_default=True,
              help="Share the radio with CrewChief: leave lap times to it, keep quiet after the line. auto: if it's running.")
@click.option("--engineer/--no-engineer", "with_engineer", default=None,
              help="A race engineer (claude -p) for the session: radio check, answers to rules, the "
                   "cool-down debrief in its words, notes afterwards. "
                   "Default: on if `claude` is found and the replay runs in real time.")
@click.pass_obj
def live_run(ctx: Ctx, replay: Path | None, speed: float, start_at: float | None, voice: str, print_only: bool,
             ref_id: str | None, learning_laps: int, threads: int, crewchief: str, with_engineer: bool | None):
    """Coach live: cue each corner on the approach, say what went wrong after it, sum up each lap.

    On the sim PC this reads iRacing (start it before or after; Ctrl+C to stop). Elsewhere, use
    --replay to hear a recording. Cues come from `iagent cues build` (built on first use).
    """
    from iagent.live.coach import Settings
    from iagent.live.run import run
    from iagent.live.sources import LIVE_CHANNELS, IrsdkSource, paced
    from iagent.live.speech import PrintVoice

    from iagent.live import crewchief as cc

    settings = Settings(learning_laps=learning_laps, crewchief=cc.resolve(crewchief))
    if print_only:
        out = PrintVoice()
    else:
        from iagent.live.voice import PocketVoice

        try:
            click.echo(f"Loading voice {voice}...")
            out = PocketVoice(ctx.workspace / "cache" / "voice", voice=voice, threads=threads)
        except RuntimeError as e:
            raise click.ClickException(str(e)) from e
        out = _SpokenToo(out)

    import shutil

    from iagent.agent import ClaudeCode
    from iagent.live.engineer import Engineer
    from iagent.live.events import AskEngineer, EngineerReply, RuleFired

    runs = ClaudeCode(ctx.workspace)
    if with_engineer is None:
        with_engineer = shutil.which(runs.claude) is not None and (not replay or speed <= 1.0)
    engineer = Engineer.over(runs) if with_engineer else None  # one conversation for the whole session
    # What the terminal shows besides the lines said: events, by name.
    shown = {
        RuleFired: lambda e: "; ".join(f"[log] {e.rule}: {a['log']}" for a in e.actions if a.get("log")),
        AskEngineer: lambda e: f"[engineer] asked: {e.kind}" + (f" ({e.rule})" if e.rule else ""),
        EngineerReply: lambda e: f"[engineer] {e.kind}: {e.text or 'nothing'}" + (f" ({e.status})" if e.status else ""),
    }

    def on_start(coach):
        rules = coach.rules.rules
        click.echo(f"Coaching {coach.ctx.session.track_name} in {coach.ctx.session.car_name}: {len(coach.ctx.plan.cues)} cues, "
                   f"following {coach.ctx.plan.ref_lap_id}." + (f" Rules: {', '.join(r.id for r in rules)}." if rules else "")
                   + (" Sharing the radio with CrewChief." if coach.settings.crewchief else "")
                   + (f" Engineer: Claude Code session {engineer.session_id}." if engineer else ""))
        for event, line in shown.items():
            coach.on(event, lambda e, line=line: (text := line(e)) and click.echo(text))
    try:
        if replay:
            src = IbtSource(replay, channels=LIVE_CHANNELS)
            run(ctx.workspace, lambda: src.session, paced(src.frames(), speed, start_at), out, settings, ref_id,
                on_start, engineer)
        else:
            live_src = IrsdkSource()
            click.echo("Waiting for iRacing...")
            live_src.connect()
            run(ctx.workspace, lambda: live_src.session, live_src.frames(), out, settings, ref_id, on_start, engineer)
    except KeyboardInterrupt:
        pass
    finally:
        if engineer is not None and not engineer.idle():
            click.echo("The engineer is writing up its notes (Ctrl+C to skip)...")
            try:
                engineer.close(wait_s=120)
            except KeyboardInterrupt:
                pass
        if hasattr(out, "close"):
            out.close()


class _SpokenToo:
    """Speak and print: the terminal shows what the voice is saying."""

    def __init__(self, voice):
        self._voice = voice
        self._print = __import__("iagent.live.speech", fromlist=["PrintVoice"]).PrintVoice()

    def duration(self, text):
        return self._voice.duration(text)

    def play(self, utterance, now):
        self._print.play(utterance, now)
        self._voice.play(utterance, now)

    def prepare(self, texts):
        return self._voice.prepare(texts)

    def close(self):
        self._voice.close()
