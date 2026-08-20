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

## Deploying it

### Through the Vercel dashboard

1. Go to [vercel.com/new](https://vercel.com/new) and import
   `lokeshgupta22/EmailAssist`.
2. Leave every setting alone. `vercel.json` already sets the output directory
   to `demo` and there is no build command to configure.
3. Deploy.

Pushing to `main` redeploys automatically after that.

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
