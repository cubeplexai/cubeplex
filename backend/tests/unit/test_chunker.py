from cubebox.middleware.citations.chunker import chunk_text


class TestChunkText:
    def test_empty_string_returns_empty_list(self):
        assert chunk_text("") == []

    def test_whitespace_only_returns_empty_list(self):
        assert chunk_text("   \n\n  ") == []

    def test_short_text_returns_single_chunk(self):
        text = "This is a short sentence."
        result = chunk_text(text)
        assert len(result) == 1
        assert result[0] == text

    def test_text_under_min_size_returns_single_chunk(self):
        text = "A" * 150
        result = chunk_text(text, min_size=200, max_size=300)
        assert len(result) == 1

    def test_splits_by_paragraph(self):
        para1 = "A" * 250
        para2 = "B" * 250
        text = f"{para1}\n\n{para2}"
        result = chunk_text(text, min_size=200, max_size=300)
        assert len(result) == 2
        assert result[0] == para1
        assert result[1] == para2

    def test_long_paragraph_splits_by_sentence_chinese(self):
        s1 = "这是第一个句子" + "内容" * 55 + "。"
        s2 = "这是第二个句子" + "内容" * 55 + "。"
        s3 = "这是第三个句子" + "内容" * 55 + "。"
        text = s1 + s2 + s3
        result = chunk_text(text, min_size=200, max_size=300)
        assert len(result) >= 2
        for chunk in result:
            assert len(chunk) <= 300

    def test_long_paragraph_splits_by_sentence_english(self):
        s1 = "First sentence. "
        s2 = "Second sentence. "
        text = s1 * 10 + s2 * 10
        result = chunk_text(text, min_size=200, max_size=300)
        assert len(result) >= 2
        for chunk in result:
            assert len(chunk) <= 300

    def test_very_long_sentence_hard_splits(self):
        text = "A" * 700
        result = chunk_text(text, min_size=200, max_size=300)
        assert len(result) >= 3
        for chunk in result:
            assert len(chunk) <= 300

    def test_short_chunks_merged(self):
        text = "Short one.\n\nShort two.\n\nShort three."
        result = chunk_text(text, min_size=200, max_size=300)
        assert len(result) == 1
        assert "Short one." in result[0]
        assert "Short three." in result[0]

    def test_mixed_paragraphs(self):
        short = "Short paragraph."
        long = "X" * 280
        text = f"{short}\n\n{long}"
        result = chunk_text(text, min_size=200, max_size=300)
        total = len(short) + 1 + len(long)
        if total <= 300:
            assert len(result) == 1
        else:
            assert len(result) == 2

    def test_respects_custom_sizes(self):
        text = "Word. " * 100
        result = chunk_text(text, min_size=100, max_size=150)
        for chunk in result:
            assert len(chunk) <= 150

    def test_sentence_boundaries_include_all_punctuation(self):
        text = "Sentence one。Sentence two！Sentence three？Sentence four.Sentence five!"
        result = chunk_text(text, min_size=10, max_size=30)
        assert len(result) >= 2
