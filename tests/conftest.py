import pytest
import os
import sys

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

@pytest.fixture
def sample_test_case_yaml():
    return """
id: SITE-FUNC-001
title: Choosing a region refreshes the branch list
pillar: functional
tags:
  - branch
  - filter
preconditions:
  - test_user_is_signed_in
steps:
  - action: select_option
    target: role=combobox[name="Region"]
    value: North Zone
assertions:
  - type: option_list_not_empty
    target: role=combobox[name="Branch"]
risk: read_only
evidence:
  - screenshot
  - assertion_log
"""

@pytest.fixture
def sample_test_pack_yaml():
    return """
pack_id: PACK-CORE-001
title: Smoke Test Suite
version: 1.0.0
target_domain: example.com
cases:
  - id: SITE-FUNC-001
    title: Home page loads
    pillar: functional
    steps:
      - action: open_page
        value: "https://example.com"
    assertions:
      - type: element_visible
        target: h1
    risk: read_only
"""
