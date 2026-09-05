CREATE VIRTUAL TABLE memory_fts USING fts5(
    entry_id UNINDEXED, subject_key, claim, tokenize = 'trigram'
);

CREATE TRIGGER memory_search_insert AFTER INSERT ON memory_search_documents BEGIN
    INSERT INTO memory_fts(entry_id, subject_key, claim)
        VALUES (NEW.entry_id, NEW.subject_key, NEW.claim);
END;

CREATE TRIGGER memory_search_update AFTER UPDATE ON memory_search_documents BEGIN
    DELETE FROM memory_fts WHERE entry_id = OLD.entry_id;
    INSERT INTO memory_fts(entry_id, subject_key, claim)
        VALUES (NEW.entry_id, NEW.subject_key, NEW.claim);
END;

CREATE TRIGGER memory_search_delete AFTER DELETE ON memory_search_documents BEGIN
    DELETE FROM memory_fts WHERE entry_id = OLD.entry_id;
END;
