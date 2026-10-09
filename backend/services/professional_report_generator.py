"""
Professional Report Generator

Stateless component that renders professional reports from structured validation context.
This component never modifies data - it only renders HTML and PDF from input models.
"""

import logging
from pathlib import Path
from typing import Optional
from uuid import UUID

from jinja2 import Environment, FileSystemLoader, select_autoescape

from backend.models.professional_report import ProfessionalReportContext

# PyMuPDF import (used as fallback if WeasyPrint is not available)
try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

logger = logging.getLogger(__name__)


class ProfessionalReportGenerator:
    """
    Generates professional HTML and PDF reports from structured validation context.
    
    This is a stateless component that:
    - Receives ProfessionalReportContext as input
    - Renders HTML using Jinja2 templates
    - Generates PDF using WeasyPrint (or falls back to PyMuPDF)
    - Returns file paths without side effects
    
    CRITICAL: This component never modifies data. It only renders and returns paths.
    """
    
    def __init__(self, template_dir: Optional[Path] = None):
        """
        Initialize the Professional Report Generator.
        
        Args:
            template_dir: Path to Jinja2 templates. Defaults to backend/templates/
        """
        print("[ProfessionalReportGenerator] Step 1 - Constructor entered")
        if template_dir is None:
            template_dir = Path("backend/templates")
        
        self.template_dir = Path(template_dir)
        print(f"[ProfessionalReportGenerator] Step 2 - Template directory: {self.template_dir}")
        self.env = Environment(
            loader=FileSystemLoader(str(self.template_dir)),
            autoescape=select_autoescape(['html', 'xml'])
        )
        print("[ProfessionalReportGenerator] Step 3 - Jinja2 environment initialized")
        logger.info(f"Initialized ProfessionalReportGenerator with templates from {self.template_dir}")
        print("[ProfessionalReportGenerator] Step 4 - Constructor completed")
    
    def generate_html(
        self,
        context: ProfessionalReportContext,
        output_path: Path
    ) -> Path:
        """
        Generate professional HTML report from validation context.
        
        Args:
            context: ProfessionalReportContext with all validation data
            output_path: Path where HTML file should be written
            
        Returns:
            Path to generated HTML file
        """
        try:
            print("[ProfessionalReportGenerator] Step 5 - generate_html() entered")
            logger.info(f"Generating professional HTML report to {output_path}")
            
            # Load template
            print("[ProfessionalReportGenerator] Step 6 - Loading template")
            template = self.env.get_template('professional_report.html')
            print("[ProfessionalReportGenerator] Step 7 - Template loaded")
            
            # Render template
            print("[ProfessionalReportGenerator] Step 8 - Rendering HTML")
            html_content = template.render(context=context)
            print("[ProfessionalReportGenerator] Step 9 - HTML rendered")
            
            # Ensure output directory exists
            print("[ProfessionalReportGenerator] Step 10 - Creating output directory")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Write HTML file
            print("[ProfessionalReportGenerator] Step 11 - Writing HTML file")
            output_path.write_text(html_content, encoding='utf-8')
            print("[ProfessionalReportGenerator] Step 12 - HTML file written")
            
            logger.info(f"Successfully generated professional HTML report at {output_path}")
            print("[ProfessionalReportGenerator] Step 13 - generate_html() completed")
            return output_path
            
        except Exception as e:
            import traceback
            print("[ProfessionalReportGenerator] FULL TRACEBACK - generate_html() failed")
            print(f"[ProfessionalReportGenerator] Exception type: {type(e).__name__}")
            print(f"[ProfessionalReportGenerator] Exception message: {e}")
            traceback.print_exc()
            logger.error(f"Failed to generate professional HTML report: {e}")
            raise
    
    def generate_pdf(
        self,
        context: ProfessionalReportContext,
        output_path: Path,
        html_path: Optional[Path] = None
    ) -> Path:
        """
        Generate professional PDF report from validation context.
        
        Args:
            context: ProfessionalReportContext with all validation data
            output_path: Path where PDF file should be written
            html_path: Optional path to existing HTML file. If None, generates HTML first.
            
        Returns:
            Path to generated PDF file
        """
        logger.info(f"Generating professional PDF report to {output_path}")
        
        # Check if any PDF generation method is available
        weasyprint_available = False
        pymupdf_available = fitz is not None
        
        try:
            # Try WeasyPrint first (preferred for HTML-to-PDF)
            try:
                return self._generate_pdf_with_weasyprint(context, output_path, html_path)
            except ImportError:
                logger.warning("WeasyPrint not available")
                weasyprint_available = False
            except Exception as e:
                logger.warning(f"WeasyPrint failed: {e}")
                weasyprint_available = False
            
            # Fall back to PyMuPDF if available
            if pymupdf_available:
                logger.info("Falling back to PyMuPDF")
                return self._generate_pdf_with_pymupdf(context, output_path, html_path)
            else:
                logger.error("Neither WeasyPrint nor PyMuPDF is available for PDF generation")
                raise ImportError("Neither WeasyPrint nor PyMuPDF is installed. Install one of them to generate PDF reports.")
                
        except Exception as e:
            logger.error(f"Failed to generate professional PDF report: {e}")
            raise
    
    def _generate_pdf_with_weasyprint(
        self,
        context: ProfessionalReportContext,
        output_path: Path,
        html_path: Optional[Path] = None
    ) -> Path:
        """Generate PDF using WeasyPrint (HTML-to-PDF conversion)."""
        from weasyprint import HTML, CSS
        
        # Generate HTML if not provided
        if html_path is None:
            html_path = output_path.with_suffix('.html')
            self.generate_html(context, html_path)
        
        # Load HTML and convert to PDF
        html_doc = HTML(filename=str(html_path))
        
        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Generate PDF with proper page settings
        html_doc.write_pdf(
            str(output_path),
            stylesheets=[CSS(string='''
                @page {
                    size: A4;
                    margin: 2cm;
                }
                
                body {
                    -weasy-print: yes;
                }
                
                .card {
                    page-break-inside: avoid;
                    break-inside: avoid;
                }
                
                .validation-card {
                    page-break-inside: avoid;
                    break-inside: avoid;
                }
                
                .screenshot-container {
                    page-break-inside: avoid;
                    break-inside: avoid;
                    max-height: 600px;
                    overflow: hidden;
                }
                
                .screenshot-container img {
                    max-width: 100%;
                    max-height: 600px;
                    object-fit: contain;
                }
                
                .no-print {
                    display: none !important;
                }
                
                .sticky-sidebar {
                    display: none !important;
                }
            ''')]
        )
        
        logger.info(f"Successfully generated PDF with WeasyPrint at {output_path}")
        return output_path
    
    def _generate_pdf_with_pymupdf(
        self,
        context: ProfessionalReportContext,
        output_path: Path,
        html_path: Optional[Path] = None
    ) -> Path:
        """Generate PDF using PyMuPDF (fallback method)."""
        import fitz
        
        # Generate HTML if not provided
        if html_path is None:
            html_path = output_path.with_suffix('.html')
            self.generate_html(context, html_path)
        
        # Check if PyMuPDF is available
        if fitz is None:
            raise ImportError("PyMuPDF (fitz) is not installed")
        
        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Use PyMuPDF's HTML to PDF conversion
        # This converts the full HTML document to multi-page PDF
        try:
            doc = fitz.open(str(html_path))
            doc.save(str(output_path))
            doc.close()
            
            logger.info(f"Successfully generated PDF with PyMuPDF HTML conversion at {output_path}")
            return output_path
        except Exception as e:
            logger.warning(f"PyMuPDF HTML conversion failed: {e}, falling back to basic PDF generation")
            
            # Fallback to basic PDF generation if HTML conversion fails
            doc = fitz.open()
            
            # Simple HTML to PDF conversion (basic implementation)
            page = doc.new_page()
            
            # Add header
            page.insert_text(
                fitz.Point(50, 50),
                f"Test Execution Report - {context.test_case_title}",
                fontsize=14,
                color=(0.1, 0.1, 0.1)
            )
            
            # Add execution info
            y_pos = 80
            page.insert_text(
                fitz.Point(50, y_pos),
                f"Execution ID: {context.execution_id}",
                fontsize=10,
                color=(0.3, 0.3, 0.3)
            )
            y_pos += 20
            page.insert_text(
                fitz.Point(50, y_pos),
                f"Status: {context.execution_status.value}",
                fontsize=10,
                color=(0.3, 0.3, 0.3)
            )
            y_pos += 20
            page.insert_text(
                fitz.Point(50, y_pos),
                f"Duration: {context.execution_duration_seconds:.2f}s",
                fontsize=10,
                color=(0.3, 0.3, 0.3)
            )
            
            # Add validation summary
            y_pos += 30
            page.insert_text(
                fitz.Point(50, y_pos),
                f"Validations: {context.total_validations} (Passed: {context.passed_count}, Failed: {context.failed_count})",
                fontsize=10,
                color=(0.3, 0.3, 0.3)
            )
            
            # Add executive summary
            y_pos += 30
            page.insert_text(
                fitz.Point(50, y_pos),
                "Executive Summary:",
                fontsize=12,
                color=(0.1, 0.1, 0.1)
            )
            y_pos += 20
            self._add_wrapped_text(page, context.executive_summary.narrative, fitz.Point(50, y_pos), 500)
            
            # Save PDF
            doc.save(str(output_path))
            doc.close()
            
            logger.info(f"Successfully generated PDF with PyMuPDF basic fallback at {output_path}")
            return output_path
    
    def _add_wrapped_text(self, page, text: str, point, max_width: int):
        """Add wrapped text to PDF page."""
        words = text.split()
        lines = []
        current_line = []
        
        for word in words:
            current_line.append(word)
            line_text = ' '.join(current_line)
            if len(line_text) > 80:  # Approximate character limit
                lines.append(' '.join(current_line[:-1]))
                current_line = [word]
        
        if current_line:
            lines.append(' '.join(current_line))
        
        y = point.y
        for line in lines:
            page.insert_text(fitz.Point(point.x, y), line, fontsize=9, color=(0.3, 0.3, 0.3))
            y += 12
    
    def generate_reports(
        self,
        context: ProfessionalReportContext,
        output_dir: Path,
        execution_id: str
    ) -> dict:
        """
        Generate both HTML and PDF reports.
        
        Args:
            context: ProfessionalReportContext with all validation data
            output_dir: Directory where reports should be written
            execution_id: Execution ID for filename
            
        Returns:
            Dictionary with paths to generated reports
        """
        try:
            print("[ProfessionalReportGenerator] Step 14 - generate_reports() entered")
            logger.info(f"Generating professional reports for execution {execution_id}")
            
            # Ensure output directory exists
            print("[ProfessionalReportGenerator] Step 15 - Creating output directory")
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            
            # Generate file paths
            print("[ProfessionalReportGenerator] Step 16 - Generating file paths")
            html_path = output_dir / f"professional_report_{execution_id}.html"
            pdf_path = output_dir / f"professional_report_{execution_id}.pdf"
            
            # Generate HTML
            print("[ProfessionalReportGenerator] Step 17 - Calling generate_html()")
            html_result = self.generate_html(context, html_path)
            print("[ProfessionalReportGenerator] Step 18 - generate_html() completed")
        
            # Generate PDF (optional - may fail if no PDF library is available)
            print("[ProfessionalReportGenerator] Step 19 - Starting PDF generation (optional)")
            pdf_path_str = None
            try:
                print("[ProfessionalReportGenerator] Step 20 - Calling generate_pdf()")
                pdf_result = self.generate_pdf(context, pdf_path, html_path)
                pdf_path_str = str(pdf_result)
                print("[ProfessionalReportGenerator] Step 21 - PDF generation completed")
            except ImportError as e:
                print(f"[ProfessionalReportGenerator] PDF generation skipped (no PDF library): {e}")
                logger.warning(f"PDF generation skipped (no PDF library available): {e}")
                pdf_path_str = None
            except Exception as e:
                import traceback
                print(f"[ProfessionalReportGenerator] PDF generation failed: {e}")
                traceback.print_exc()
                logger.error(f"PDF generation failed: {e}")
                pdf_path_str = None
        
            print("[ProfessionalReportGenerator] Step 22 - Building result dictionary")
            result = {
                "html_path": str(html_result),
                "execution_id": execution_id
            }
            
            if pdf_path_str:
                result["pdf_path"] = pdf_path_str
            
            print("[ProfessionalReportGenerator] Step 23 - generate_reports() completed")
            return result
            
        except Exception as e:
            import traceback
            print("[ProfessionalReportGenerator] FULL TRACEBACK - generate_reports() failed")
            print(f"[ProfessionalReportGenerator] Exception type: {type(e).__name__}")
            print(f"[ProfessionalReportGenerator] Exception message: {e}")
            traceback.print_exc()
            raise
