# Host Council of Sumer on Render

The free Render Blueprint runs five advisors' independent decisions inside one
web process using the existing in-process transport. Each advisor keeps its own
goals, rulebook, beliefs, and votes. Bayleaf decisions and ElevenLabs voices stay
live. The hosted demo does not demonstrate network transport over SLIM.
Render provides `PORT`; local `WEB_PORT`
overrides it when set. The image uses Python 3.13 and the committed `uv.lock`.

## Deploy

1. Push this deployment configuration to GitHub.
2. Open [Deploy to Render](https://render.com/deploy?repo=https://github.com/ericxu1070/council-of-sumer),
   or choose **New → Blueprint** in the Render dashboard and select the repository
   and branch containing `render.yaml`. Before the deployment PR is merged, select
   its branch rather than `main`.
3. Review the `council-of-sumer` web service. The Blueprint selects the **Free**
   instance type and Oregon region. It uses `sumer web` and `TRANSPORT=inproc`
   to fit within the free instance's memory budget.
4. Enter `BAYLEAF_API_KEY` and `ELEVENLABS_API_KEY` when prompted. These values go
   into Render's environment settings, never into the repository. Bayleaf's
   URL and model default to the values in `.env.example`; change them in Render
   if your account uses a different endpoint or model.
5. Deploy. Once Render marks the service **Live**, open the HTTPS URL shown at
   the top of its service page. That is the demo link to share.

The health check requests `/api/meta` without making model or voice calls.
Check that the UI loads and `/api/meta` reports `transport: "inproc"` and `voice: true`.
Start one game to verify Bayleaf
decisions and ElevenLabs playback. Browser audio may need a click to start.

Automatic redeploys are disabled so an unrelated code push does not interrupt
the demo. Deploy future changes using **Manual Deploy → Deploy latest commit**.
The Blueprint overrides the image's default command with `sumer web`. The local
Observe collector is disabled for this small hosted demo.

For the full networked SLIM demonstration, run the default image command
`sumer up` with `TRANSPORT=slim` on a larger instance. The eight local processes
measured about 774 MiB combined RSS, above Free's 512 MB limit. Review any paid
plan before upgrading; the free demo needs no upgrade.

## Demo behavior and limits

This is one shared live chamber. Visitors share the active game, controls, and
run history; the app does not create separate games for each visitor. Keep one
instance and one web worker. Visitors' live model and voice requests use your
API accounts.

Free Render instances sleep after 15 idle minutes, and waking can take about
a minute. Open the demo before presenting. Their local files are ephemeral:
restarts, redeploys, and sleep erase saved runs and model/audio caches. Export
any run you want to keep before then. A paid instance with a persistent disk
mounted at `/app/.cache` preserves these files; upgrading is optional and paid.

`LLM_BUDGET_USD` is the app's existing Bayleaf budget, not a combined provider
quota or an ElevenLabs limit. Use provider-side credit limits for total API
spending, including voice generation.

If the deploy fails, check Render's build/runtime logs. A killed process or
out-of-memory error may mean the five advisors need a larger instance; review
the paid plan before upgrading. Failed API calls usually indicate the key,
model access, credits, or endpoint settings need adjustment.
`sumer up` now reports which child process exited and returns a failure status,
so Render can distinguish an advisor crash from a clean shutdown.

## Local container check (requires Docker)

```bash
docker build -t council-of-sumer .
docker run --rm --env-file .env -e TRANSPORT=inproc -e WEB_PORT=10000 -p 10000:10000 council-of-sumer sumer web
```

Open `http://localhost:10000`. The `.env` file is passed at runtime and excluded
from the image. No Docker installation is needed on your laptop to deploy:
Render builds the image remotely.

References: [Render Blueprints](https://render.com/docs/blueprint-spec),
[Docker services](https://render.com/docs/docker),
[Free service limits](https://render.com/docs/free).
