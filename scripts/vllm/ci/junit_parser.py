"""JUnit XML parser for extracting individual test results.

Adapted from vllm_ci_runner.py parse_junit_xml (lines 304-377).
"""

import logging
from xml.etree import ElementTree as ET

from .models import TestResult

log = logging.getLogger(__name__)

MAX_MESSAGE_LEN = 500


def parse_junit_xml(
    xml_bytes: bytes,
    job_name: str,
    job_id: str,
    step_id: str,
    build_number: int,
    pipeline: str,
    date: str,
) -> list[TestResult]:
    """Parse JUnit XML content into TestResult objects.

    Args:
        xml_bytes: Raw XML content
        job_name: Buildkite job/step name
        job_id: Buildkite job UUID
        step_id: Buildkite step UUID (from job.step.id)
        build_number: Build number
        pipeline: Pipeline key ("amd-ci" or "ci")
        date: ISO date string

    Returns:
        List of TestResult objects
    """
    results = []

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log.warning("Failed to parse JUnit XML from job %s: %s", job_name, e)
        return results

    # Handle both <testsuites> and <testsuite> as root
    if root.tag == "testsuites":
        suites = root.findall("testsuite")
    elif root.tag == "testsuite":
        suites = [root]
    else:
        suites = root.findall(".//testsuite")
        if not suites:
            return results

    for suite in suites:
        for tc in suite.findall("testcase"):
            name = tc.get("name", "unknown")
            classname = tc.get("classname", "")
            tc_time = float(tc.get("time", 0))

            failure = tc.find("failure")
            error = tc.find("error")
            skipped = tc.find("skipped")

            if error is not None:
                status = "error"
                msg = error.get("message", "") or (error.text or "")
            elif failure is not None:
                status = "failed"
                msg = failure.get("message", "") or (failure.text or "")
            elif skipped is not None:
                msg = skipped.get("message", "") or ""
                skip_type = skipped.get("type", "")
                if "xfail" in msg.lower() or "xfail" in skip_type.lower():
                    status = "xfailed"
                else:
                    status = "skipped"
            else:
                status = "passed"
                msg = ""
                # Check for xpass via pytest properties
                props = tc.find("properties")
                if props is not None:
                    for prop in props.findall("property"):
                        if prop.get("name") == "xpass":
                            status = "xpassed"
                            break

            test_id = f"{classname}::{name}" if classname else name

            results.append(TestResult(
                test_id=test_id,
                name=name,
                classname=classname,
                status=status,
                duration_secs=tc_time,
                failure_message=msg[:MAX_MESSAGE_LEN],
                job_name=job_name,
                job_id=job_id,
                step_id=step_id,
                build_number=build_number,
                pipeline=pipeline,
                date=date,
            ))

    return results
