import io
import zipfile

from app.ingestion.extractors import extract_vsdx

_PAGE1_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<PageContents xmlns="http://schemas.microsoft.com/office/visio/2012/main">
  <Shapes>
    <Shape ID="1" Type="Shape">
      <Text>Submit request</Text>
    </Shape>
    <Shape ID="2" Type="Shape">
      <Text>Manager approves</Text>
    </Shape>
    <Shape ID="3" Type="Shape">
    </Shape>
  </Shapes>
  <Connects>
    <Connect FromSheet="3" FromCell="BeginX" ToSheet="1" ToCell="PinX"/>
    <Connect FromSheet="3" FromCell="EndX" ToSheet="2" ToCell="PinX"/>
  </Connects>
</PageContents>
"""


def _build_vsdx_bytes(page_xml: str = _PAGE1_XML) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("visio/pages/page1.xml", page_xml)
    return buffer.getvalue()


def test_extract_vsdx_returns_shape_text_blocks():
    blocks = extract_vsdx(_build_vsdx_bytes())
    shape_blocks = [b for b in blocks if "shape" in b.location and "connector" not in b.location]

    assert len(shape_blocks) == 2
    assert shape_blocks[0].location == "page 1, shape 1"
    assert shape_blocks[0].content == "Submit request"
    assert shape_blocks[1].location == "page 1, shape 2"
    assert shape_blocks[1].content == "Manager approves"


def test_extract_vsdx_derives_connector_relationship_using_shape_labels():
    blocks = extract_vsdx(_build_vsdx_bytes())
    connector_blocks = [b for b in blocks if "connector" in b.location]

    assert len(connector_blocks) == 1
    assert connector_blocks[0].location == "page 1, connector 3"
    assert connector_blocks[0].content == "Connector: 'Submit request' -> 'Manager approves'"


def test_extract_vsdx_ignores_connect_with_only_one_target():
    page_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<PageContents xmlns="http://schemas.microsoft.com/office/visio/2012/main">
  <Shapes>
    <Shape ID="1" Type="Shape"><Text>Lonely shape</Text></Shape>
  </Shapes>
  <Connects>
    <Connect FromSheet="9" FromCell="BeginX" ToSheet="1" ToCell="PinX"/>
  </Connects>
</PageContents>
"""
    blocks = extract_vsdx(_build_vsdx_bytes(page_xml))
    assert all("connector" not in b.location for b in blocks)


def test_extract_vsdx_skips_shapes_with_no_text():
    page_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<PageContents xmlns="http://schemas.microsoft.com/office/visio/2012/main">
  <Shapes>
    <Shape ID="1" Type="Shape"></Shape>
  </Shapes>
</PageContents>
"""
    blocks = extract_vsdx(_build_vsdx_bytes(page_xml))
    assert blocks == []


def test_extract_vsdx_handles_multiple_pages():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "visio/pages/page1.xml",
            """<?xml version="1.0"?><PageContents xmlns="http://schemas.microsoft.com/office/visio/2012/main">
<Shapes><Shape ID="1"><Text>Page one shape</Text></Shape></Shapes></PageContents>""",
        )
        archive.writestr(
            "visio/pages/page2.xml",
            """<?xml version="1.0"?><PageContents xmlns="http://schemas.microsoft.com/office/visio/2012/main">
<Shapes><Shape ID="1"><Text>Page two shape</Text></Shape></Shapes></PageContents>""",
        )

    blocks = extract_vsdx(buffer.getvalue())
    locations = [b.location for b in blocks]
    assert "page 1, shape 1" in locations
    assert "page 2, shape 1" in locations
