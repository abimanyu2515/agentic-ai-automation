import { NextRequest, NextResponse } from 'next/server';
import * as fs from 'fs/promises';
import * as path from 'path';

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ executionId: string }> }
) {
  console.log("--- GET REPORT ROUTE HIT ---");
  try {
    const { executionId } = await params;
    console.log("GET REPORT executionId:", executionId);
    const reportPath = path.join(
      process.cwd(),
      'public',
      'artifacts',
      executionId,
      'report.html'
    );

    const fileBuffer = await fs.readFile(reportPath);
    return new Response(fileBuffer, {
      headers: {
        'Content-Type': 'text/html; charset=utf-8',
      },
    });
  } catch (error: any) {
    return NextResponse.json({ error: 'Report not found' }, { status: 404 });
  }
}
