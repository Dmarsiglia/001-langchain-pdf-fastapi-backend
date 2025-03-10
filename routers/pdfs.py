from typing import List
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
import schemas
import crud
from database import SessionLocal
from uuid import uuid4
import os
import cloudinary
import cloudinary.uploader
from cloudinary import config as cloudinary_config
from dotenv import load_dotenv

# Necessary imports for langchain summarization
from langchain import PromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.chains import LLMChain

# Necessary imports to chat with a PDF file
from langchain.document_loaders import PyPDFLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain.vectorstores import FAISS
from langchain.chains import RetrievalQA
from schemas import QuestionRequest

# Cargar variables de entorno
load_dotenv()

# Configurar Cloudinary
cloudinary_config(
    cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
    api_key=os.getenv('CLOUDINARY_API_KEY'),
    api_secret=os.getenv('CLOUDINARY_API_SECRET')
)

llm = ChatGoogleGenerativeAI(model="gemini-1.5-pro")

router = APIRouter(prefix="/pdfs")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.post("", response_model=schemas.PDFResponse, status_code=status.HTTP_201_CREATED)
def create_pdf(pdf: schemas.PDFRequest, db: Session = Depends(get_db)):
    return crud.create_pdf(db, pdf)

@router.post("/upload", response_model=schemas.PDFResponse, status_code=status.HTTP_201_CREATED)
async def upload_pdf(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Solo se permiten archivos PDF")
    
    try:
        # Subir archivo a Cloudinary
        result = cloudinary.uploader.upload(
            file.file,
            folder="pdfs",
            resource_type="raw",
            access_mode="public",
            public_id=f"pdf_{file.filename}",
            allowed_formats=["pdf"]
        )

        # Crear registro en la base de datos
        pdf = schemas.PDFCreate(
            name=file.filename,
            file=result['secure_url'],  # Asegúrate de usar 'secure_url'
            selected=False
        )
        
        return crud.create_pdf(db, pdf)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("", response_model=List[schemas.PDFResponse])
def get_pdfs(selected: bool = None, db: Session = Depends(get_db)):
    return crud.read_pdfs(db, selected)

@router.get("/{id}", response_model=schemas.PDFResponse)
def get_pdf_by_id(id: int, db: Session = Depends(get_db)):
    pdf = crud.read_pdf(db, id)
    if pdf is None:
        raise HTTPException(status_code=404, detail="PDF not found")
    return pdf

@router.put("/{id}", response_model=schemas.PDFResponse)
def update_pdf(id: int, pdf: schemas.PDFRequest, db: Session = Depends(get_db)):
    updated_pdf = crud.update_pdf(db, id, pdf)
    if updated_pdf is None:
        raise HTTPException(status_code=404, detail="PDF not found")
    return updated_pdf

@router.delete("/{id}", status_code=status.HTTP_200_OK)
def delete_pdf(id: int, db: Session = Depends(get_db)):
    pdf = crud.read_pdf(db, id)
    if pdf is None:
        raise HTTPException(status_code=404, detail="PDF not found")
    
    try:
        # Extraer el public_id del URL de Cloudinary
        public_id = pdf.file.split('/')[-1].split('.')[0]
        print(public_id)
        # Eliminar el archivo de Cloudinary
        cloudinary.uploader.destroy(f"pdfs/{public_id}.pdf", resource_type="raw")
        #elimina el archivo de la carpeta local
        # if os.path.exists(pdf.file):
        #     os.remove(pdf.file)
        # Eliminar de la base de datos
        if not crud.delete_pdf(db, id):
            raise HTTPException(status_code=404, detail="Error deleting PDF from database")
        
        return {"message": "PDF successfully deleted from Cloudinary and database"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# LANGCHAIN
langchain_llm = ChatGoogleGenerativeAI(model="gemini-1.5-pro", temperature=0)

summarize_template_string = """
        Provide a summary for the following text:
        {text}
"""

summarize_prompt = PromptTemplate(
    template=summarize_template_string,
    input_variables=['text'],
)

summarize_chain = LLMChain(
    llm=langchain_llm,
    prompt=summarize_prompt,
)

@router.post('/summarize-text')
async def summarize_text(text: str):
    summary = summarize_chain.run(text=text)
    return {'summary': summary}


# Ask a question about one PDF file
@router.post("/qa-pdf/{id}")
def qa_pdf_by_id(id: int, question_request: QuestionRequest,db: Session = Depends(get_db)):
    pdf = crud.read_pdf(db, id)
    if pdf is None:
        raise HTTPException(status_code=404, detail="PDF not found")
    print(pdf.file)
    # Usar la ruta local del archivo PDF
    loader = PyPDFLoader(pdf.file)
    document = loader.load()
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=3000,chunk_overlap=400)
    document_chunks = text_splitter.split_documents(document)
    embeddings = GoogleGenerativeAIEmbeddings(model="models/embedding-001")
    stored_embeddings = FAISS.from_documents(document_chunks, embeddings)
    QA_chain = RetrievalQA.from_chain_type(llm=llm,chain_type="stuff",retriever=stored_embeddings.as_retriever())
    question = question_request.question
    answer = QA_chain.run(question)
    return answer