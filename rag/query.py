import os
from langchain_openai import OpenAIEmbeddings
from langchain_chroma import Chroma
from dotenv import load_dotenv

load_dotenv()

embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
vectorstore = Chroma(persist_directory="chroma_db", embedding_function=embeddings)


def get_relevant_chunks(question, subject=None, top_k=3):
    """
    Retrieve NCTB SSC textbook chunks filtered by subject.
    
    Args:
        question (str): Student's question
        subject (str): "biology", "physics", "chemistry", "math" — filter by subject
        top_k (int): Number of chunks to return
    
    Returns:
        str: Concatenated text from relevant chunks
    """
    
    if not subject:
        # No subject = casual chat, skip RAG
        return ""
    
    filter_dict = {"subject": subject}
    
    try:
        results = vectorstore.similarity_search(
            question,
            k=top_k,
            filter=filter_dict
        )
        
        if not results:
            return ""
        
        # Format with chapter labels
        chunks = []
        for doc in results:
            subj = doc.metadata.get("subject", "?")
            chapter = doc.metadata.get("chapter", "")
            
            label = f"[SSC {subj.capitalize()}"
            if chapter:
                label += f" — {chapter}"
            label += "]"
            
            chunks.append(f"{label}\n{doc.page_content}")
        
        return "\n\n---\n\n".join(chunks)
    
    except Exception as e:
        print(f"[RAG ERROR] {e}")
        return ""


def get_chapters_for_question(question, subject="biology", top_k=3):
    """Returns just the chapter names from RAG search, not the full text."""
    if not subject:
        return []
    
    filter_dict = {"subject": subject}
    
    try:
        results = vectorstore.similarity_search(question, k=top_k, filter=filter_dict)
        chapters = []
        seen = set()
        for doc in results:
            ch = doc.metadata.get("chapter", "")
            if ch and ch not in seen:
                chapters.append(ch)
                seen.add(ch)
        return chapters
    except Exception as e:
        print(f"[CHAPTER ERROR] {e}")
        return []