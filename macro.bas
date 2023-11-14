Sub UnmergeAndFillData()
    Dim ws As Worksheet
    Dim cell As Range
    Dim mergedRange As Range
    Dim mergedValue As Variant

    ' Set the worksheet where you want to unmerge cells
    Set ws = ThisWorkbook.Sheets("INF st I") ' Change "Sheet1" to your sheet name

    ' Loop through each cell in the worksheet
    For Each cell In ws.UsedRange
        ' Check if the cell is part of a merged range
        If cell.MergeCells Then
            ' Store the merged range
            Set mergedRange = cell.MergeArea
            ' Store the value of the merged cell
            mergedValue = cell.Value
            ' Unmerge the cells
            mergedRange.UnMerge
            ' Fill all cells within the merged range with the merged value
            mergedRange.Value = mergedValue
        End If
    Next cell
End Sub