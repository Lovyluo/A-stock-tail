"""Fixed six-request daily freeze and independent offline verification."""
import argparse
import json

from overnight_quant.data.daily_freeze import capture, final_success, read_anchored_json, rejected_final, verify


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--date")
    parser.add_argument("--codes")
    parser.add_argument("--approved-sha")
    parser.add_argument("--output")
    parser.add_argument("--authorization")
    parser.add_argument("--authorization-sha256")
    parser.add_argument("--test-fixture")
    parser.add_argument("--test-batch-id")
    parser.add_argument("--verify")
    parser.add_argument("--anchors")
    parser.add_argument("--anchors-sha256")
    args = parser.parse_args(argv)
    try:
        if args.verify:
            if args.network or args.test_fixture:
                raise ValueError("verification_is_offline_only")
            anchors = read_anchored_json(args.anchors, args.anchors_sha256)
            if "anchors" in anchors:
                anchors = anchors["anchors"]
            result = verify(args.verify, anchors)
            code = 0 if result.get('status') == 'EXECUTION_COMPLETE' and final_success(result) else 2
        else:
            authorization = ({"batch_id": args.test_batch_id} if args.test_fixture else
                             read_anchored_json(args.authorization, args.authorization_sha256) if args.network else {})
            result = capture(day=args.date, codes=args.codes, approved_sha=args.approved_sha,
                             output=args.output, authorization=authorization,
                             network=args.network, fixture=args.test_fixture)
            idle = not args.network and not args.test_fixture and result['status'] == 'NETWORK_NOT_REQUESTED'
            success = result['status'] == 'EXECUTION_COMPLETE' and final_success(result)
            code = 0 if idle or success else 2
            if result['status'] == 'EXECUTION_COMPLETE' and not success:
                result = {**rejected_final(result), 'status': 'FINAL_VERIFICATION_FAILED'}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return code
    except Exception as exc:
        print(json.dumps({"status": "INPUT_REJECTED", "error": f"{type(exc).__name__}:{exc}"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
