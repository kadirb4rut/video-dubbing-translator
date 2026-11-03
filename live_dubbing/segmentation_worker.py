"""Private JSON-lines adapter; executed only in the SaT isolated environment."""
import argparse
import contextlib
import json
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True)
    parser.add_argument('--tokenizer', required=True)
    args = parser.parse_args()
    with contextlib.redirect_stdout(sys.stderr):
        import onnxruntime as ort
        from wtpsplit import SaT
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        model = SaT(args.model, tokenizer_name_or_path=args.tokenizer,
            from_pretrained_kwargs={'local_files_only': True},
            ort_providers=['CPUExecutionProvider'], ort_kwargs={'sess_options': options})
    for line in sys.stdin:
        identifier = None
        try:
            value = json.loads(line)
            identifier, text = value['id'], value['text']
            if not isinstance(text, str) or len(text) > 8000:
                raise ValueError('Invalid segmentation request')
            with contextlib.redirect_stdout(sys.stderr):
                scores = [float(x) for x in model.predict_proba(text)] if text else []
            response = {'id': identifier, 'ok': True, 'scores': scores}
        except Exception as exc:
            response = {'id': identifier, 'ok': False, 'error': f'{type(exc).__name__}: {exc}'}
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
