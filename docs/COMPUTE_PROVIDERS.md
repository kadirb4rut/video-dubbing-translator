# Choose a GPU in the web GUI

Run `./start_gui.sh`. Under **Offline dubbing · GPU provider**, choose **Local**, **Colab CLI — G4 GPU**, or **Modal — full video dubbing**. Choose a video, source/target languages and a translation model, then press **Start Dubbing**. If the cloud connection is not ready, setup runs first and the selected job continues when ready. You can also use **Connect & prepare GPU** first.

## Account connection and preparation

Missing cloud clients are installed in a separate per-user Python environment. The local AI environment is not upgraded or replaced. Account authorization links, confirmation prompts and authorization-code fields appear in the GUI. Complete Google/Modal consent yourself. No notebook, remote folder path or manually created session is needed for the recommended web workflow. Account credentials remain with the provider clients; they are not included in uploaded code or project settings.

| Provider | Processing location | Persistent data |
| --- | --- | --- |
| Local | Existing full pipeline on this computer's available CPU/GPU | Existing local models and outputs |
| Colab CLI | Complete offline pipeline on the dedicated G4 runtime; full Live service is also available | Temporary VM model cache |
| Modal | Complete offline pipeline on a private authenticated GPU app | `video-dubbing-full-pipeline` model Volume |

**The current GUI keeps connected Colab and Modal resources open until you press Disconnect GPU.** Finishing/cancelling/failing a job, closing a Live browser, closing the GUI tab, or closing the GUI server does not request VM/app shutdown. Provider quotas, outages and runtime limits still apply. Allocated resources can continue consuming credits while idle. Disconnect before leaving resources unused.

Changing providers does not silently close an existing connection. Use **Disconnect GPU** before preparing another provider. The Disconnect action cancels active work and releases this application's managed Colab runtime and Modal app. Modal's persistent model Volume remains and its storage is billed independently of GPU runtime.

## GUI restart and connection state

Provider lifetime is separate from the local GUI process. A new GUI server does
not automatically adopt an existing Colab connection's in-memory readiness
state. A previous restart check found the panel disconnected/Disconnect disabled
while an older launcher/tunnel/runtime remained active. This is a known recovery
limitation, not proof that the VM stopped. Check `colab sessions` before creating
another runtime. When finished, explicitly stop only this application's dedicated
VM with `colab stop --session video-dubbing-g4` if GUI disconnection is unavailable;
that ends the entire selected VM. The saved Modal app can be recovered during a
later Connect action when its non-secret identifiers and active deployment match.

Edited Python GUI code takes effect after restarting the server. Reload the page
after a server restart to obtain its new per-process request token. The normal
product port is 8765; development preview port 18765 is not a second GPU-control
workflow to run alongside an existing job.

## Colab CLI

The GUI reuses or creates only the dedicated `video-dubbing-g4` session. It uploads an explicit application-code bundle into `/content/.vdt-managed`, prepares an isolated Linux GPU environment, and checks/downloads the selected missing models. Existing development repositories and model folders are not overwritten. New cloud runtime dependencies are separate from the stable local environment.

Models are cached on the Colab VM. They are reused while the VM remains available and must be downloaded again after it is terminated. Connecting may still require ordinary Colab CLI account sign-in.

For offline jobs, the selected local video is uploaded through the authenticated Colab CLI. The existing pipeline runs on G4, and the finished MP4 is downloaded to this computer. The G4 remains connected for the next job. **Disconnect GPU** terminates the whole selected VM, including other notebooks attached to that dedicated VM; files held only in `/content` are then lost. Do not share the dedicated runtime with unrelated work.

For Live Dubbing, Chrome runs on this computer. Only the selected, permitted tab audio goes through a loopback-only SSH tunnel to the Colab service. Before opening the browser, the GUI refreshes the small Live service module; an older service is restarted only when no capture is active. This does not replace model caches or terminate the G4. Closing the browser closes its tunnel and service work without terminating the G4. Explicit capture permission remains required. A separate VNC/SSH connection can conflict with Colab's one-SSH-session limit.

The upstream Colab CLI supports macOS/Linux. The GUI disables Colab on Windows. GPU availability depends on the user's account and current capacity. Google's [Colab restrictions](https://research.google.com/colaboratory/faq.html#resource-limits) still apply.

## Modal

Connection setup builds and deploys this application's private `video-dubbing-full-pipeline` app, prepares models on its Volume, and verifies an allocated GPU before reporting ready. No public web endpoint is opened. The connected app keeps one GPU container available until **Disconnect GPU** stops that app. A10G/L4 allocation depends on Modal availability/account limits; the GUI must not report ready while allocation is pending.

Jobs are submitted through the authenticated SDK to that existing app. Input/output transfer is automatic. Completing or cancelling an input leaves the app connected for another video. The app lifetime is independent of the GUI process. A later connection can recover a still-active managed app through the saved non-secret app/function identifiers.

The model Volume remains after disconnection and can incur storage charges. Disconnect stops compute; it does not delete models or account credentials. Modal Live Dubbing is not offered.

The older `modal-tts` CLI/desktop path is retained for existing deployments of `modal_app.py`; it offloads only VoxCPM2 and retains its original function autoscaler behavior. It is separate from the new full-pipeline Modal connection.

## Models and files

Community-1 is optional and requires accepted Hugging Face terms. Enter a read token in the optional speaker-model panel if needed. It is used only for setup and is not saved in settings. Without a cached model, existing per-segment reference behavior is used. Exactly the selected Hy-MT2 model is prepared.

Cloud input formats such as WebM are converted to an MP4 working copy remotely for the existing H.264/AAC video writer. The local original is unchanged. Cloud results are saved as MP4 under `vocal-remover/final_video/final`. GPU disconnection does not affect a completed local video. **Download video** returns a direct attachment; normal playback supports HTTP range requests. The GUI restores its last completed output after restart.

## Advanced CLI use

The existing prepared-runtime Colab CLI remains available:

```sh
.venv/bin/python scripts/run_dubbing.py input.mp4 --compute colab --session my-g4 --remote-project /content/my-prepared-project --source-language en --target-language tr
```

It now leaves that VM running. Explicitly stop it when finished:

```sh
colab stop --session my-g4
```

A full Modal job can use a connection already prepared in the web GUI:

```sh
.venv/bin/python scripts/run_dubbing.py input.mp4 --compute modal --target-language tr
```

Use **Disconnect GPU** in the web GUI to end the managed connection. The standalone Live launcher keeps its selected Colab VM after browser closure as well.

## Web controls and progress

Advanced model/device, speaker, glossary and lip-sync options are under
**Advanced model, speaker and lip-sync settings** in the Offline panel. Live and
Offline provider choices remain independent. Action buttons show waiting
feedback and the activity area follows actual server job/cloud states. A request
being submitted is not a completion claim; percentages and ETAs are not inferred.
Use **Connection log** and **Process Log** for detailed diagnostics. The Live
extension retains its existing Start/capture/Finish/Stop flow. See
[UI verification](release/UI_UX_VERIFICATION_20261101.md).

## Verification scope

See [the November 1 evidence](release/GUI_COMPUTE_E2E_20261101.md). A complete English NASA WebM was translated to Turkish through the actual web GUI and Colab CLI/G4, returned locally and decoded fully. Larger Hy-MT2 models and clean cross-platform installations have not been certified. Historical release evidence records the former automatic shutdown policy; the current default is explicit disconnection only.
