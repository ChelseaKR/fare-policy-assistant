import pytest

from assistant import config
from assistant.ingest import Chunk
from assistant.retrieve import Retriever


def make_chunk(**kw) -> Chunk:
    defaults = dict(
        chunk_id="mst-fares#0",
        doc_id="mst-fares",
        agency="MST",
        agency_full="Monterey-Salinas Transit",
        doc_title="Fares",
        url="https://mst.org/fares/",
        fetch_date="2026-06-12",
        language="en",
        section="Discount Eligibility",
        text=(
            "Discount fare for: 18 years and under, 65 years and older, "
            "individuals with disabilities, Medicare Card holders, Veterans. "
            "Single ride regular fare is $2.00 and discount fare is $1.00."
        ),
    )
    defaults.update(kw)
    return Chunk(**defaults)


@pytest.fixture
def chunks() -> list[Chunk]:
    # BM25 needs a handful of documents for sane IDF; pad with realistic filler.
    fillers = [
        ("mst-fares#1", "mst-fares", "MST", "GoCard",
         "The GoCard is a stored value card with 10% bonus on reload."),
        ("mst-fares#2", "mst-fares", "MST", "Pass Outlets",
         "GoPasses available at customer service locations in Monterey county."),
        ("sacrt-fares#0", "sacrt-fares", "SacRT", "Light Rail",
         "Single ride tickets are valid for 90 minutes from validation on light rail."),
        ("sbmtd-fares-passes#0", "sbmtd-fares-passes", "SBMTD", "Transfers",
         "Transfers are issued by the driver and valid for 60 minutes on the second bus."),
    ]
    return [
        make_chunk(),
        make_chunk(
            chunk_id="yolobus-fares#0",
            doc_id="yolobus-fares",
            agency="Yolobus",
            agency_full="Yolo County Transportation District",
            doc_title="Fares",
            url="https://yolobus.com/fares/",
            section="Yolobus Fixed Route Bus Fares",
            text=(
                "Youth ages 0-18 ride free. Senior/Disabled 62+ local fare $1.00. "
                "Regular adult local fare $2.00, express $3.25."
            ),
        ),
        *[
            make_chunk(chunk_id=cid, doc_id=did, agency=agency, section=section, text=text)
            for cid, did, agency, section, text in fillers
        ],
    ]


@pytest.fixture
def retriever(chunks) -> Retriever:
    return Retriever(chunks, config.RetrievalConfig(top_k=3, min_confidence=0.5))
