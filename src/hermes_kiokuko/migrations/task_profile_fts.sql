CREATE VIRTUAL TABLE task_profile_fts USING fts5(profile_id UNINDEXED,text,tokenize='trigram');
CREATE TRIGGER task_profile_document_insert AFTER INSERT ON task_profile_documents BEGIN
    INSERT INTO task_profile_fts(profile_id,text) VALUES(NEW.profile_id,NEW.text);
END;
CREATE TRIGGER task_profile_document_update AFTER UPDATE ON task_profile_documents BEGIN
    DELETE FROM task_profile_fts WHERE profile_id=OLD.profile_id;
    INSERT INTO task_profile_fts(profile_id,text) VALUES(NEW.profile_id,NEW.text);
END;
CREATE TRIGGER task_profile_document_delete AFTER DELETE ON task_profile_documents BEGIN
    DELETE FROM task_profile_fts WHERE profile_id=OLD.profile_id;
END;
