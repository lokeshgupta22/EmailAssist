# Deploying the demo

## Why the application itself is not deployed

EmailAssist runs a language model on the machine it is installed on. That is
the point of it, not an implementation detail: the threat model, the privacy
guarantees and the "switch off the network and it still works" demonstration
all rest on it.

A serverless host cannot support that. Vercel functions have no persistent
process to keep a model resident, a deployment size limit far below a 2.5 GB
model, and an execution ceiling shorter than a single analysis takes. Making it
run there would mean calling a hosted model API instead — at which point the
email content leaves the machine, the threat model no longer holds, and the
project is the thing it was built not to be.

So what is deployed is a **recorded demo**: the real interface, showing real
output, captured from real local runs, and labelled as such on the page.

## What gets deployed

The `demo/` directory, as static files. No build step, no server, no
dependencies.

```
demo/
  index.html      the demo page
  demo.js         thread picker; hands the chosen result to the shared renderer
  demo.css        styles used only by the demo
  style.css       copied from app/static — the application's own stylesheet
  render.js       copied from app/static — the application's own renderer
  results.json    real captured output from the pipeline
  capture.py      the script that produced results.json
  sync_assets.py  keeps the two copied files in step with their sources
```

Two files are copies, because Vercel serves the directory as-is and cannot
reach outside it. Copies go stale, so `demo/sync_assets.py` is the only way
they are updated and `tests/test_demo.py` fails if they drift.

## Stopping Vercel from deploying the application

The first attempt failed, and the failure is worth recording because it is the
same mistake the whole project is arranged to avoid.

Vercel detects frameworks automatically. It found `app/main.py`, recognised a
FastAPI application, ignored the static configuration and deployed it as a
serverless function. The function then crashed on its first request:

```
ModuleNotFoundError: No module named 'fastapi'
```

The missing dependency is beside the point. Even with every dependency
installed it could not have worked: there is no Ollama in a Vercel function, no
persistent process to hold a model, and no 2.5 GB of room to put one.

Two independent measures stop it:

1. `vercel.json` sets `"framework": null`, which disables preset detection and
   overrides whatever the project settings hold.
2. `.vercelignore` uses the allowlist form — ignore everything, re-include only
   `demo` and `vercel.json` — so `app/` and `requirements.txt` are never
   uploaded. There is nothing left for detection to find.

`tests/test_demo.py` asserts both, so a later change cannot quietly turn the
application back into a function.

## Deploying it

### Through the Vercel dashboard

1. Go to [vercel.com/new](https://vercel.com/new) and import
   `lokeshgupta22/EmailAssist`.
2. Set **Framework Preset** to **Other**. It may otherwise be pre-filled from
   detection; `vercel.json` overrides it, but there is no reason to leave a
   contradiction in the settings.
3. Leave **Root Directory** as `./` — `vercel.json` lives there and points the
   output at `demo`.
4. Leave Build and Install commands empty.
5. Deploy.

Pushing to `main` redeploys automatically after that.

If a project already exists from a failed import, open
**Settings → General**, set Framework Preset to **Other**, clear any Build and
Install command overrides, then **Deployments → Redeploy** with the build cache
disabled.

### From the command line

```bash
npx vercel --prod
```

### Any other static host

There is nothing Vercel-specific about the content. `demo/` can be served by
GitHub Pages, Netlify, Cloudflare Pages or `python -m http.server` inside the
directory.

## Refreshing the recorded results

After changing the pipeline, the prompts or the interface:

```bash
python -m demo.sync_assets   # re-copy the shared stylesheet and renderer
python -m demo.capture       # re-run the real pipeline over the test threads
make test                    # the demo tests check the two stayed in step
```

`capture.py` needs Ollama running, because it produces the results by actually
analysing the threads. That is the whole point — there is no path in this
repository that fabricates demo output.

## Headers

`vercel.json` applies the same content security policy the application sends,
plus `nosniff`, `DENY` framing and `no-referrer`. The demo loads no external
script, style, font or image, so the policy holds without exceptions.
