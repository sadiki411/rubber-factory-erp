import { PlusOutlined } from '@ant-design/icons'
import { App, Button, Empty, Input, Select, Typography } from 'antd'
import { QueryClientContext } from '@tanstack/react-query'
import { useContext, useMemo, useRef, useState } from 'react'
import { qualityApi } from '../api/client'
import type { QualityEmployee, QualityEmployeeRole } from '../types'

type EmployeePurpose = 'INSPECTOR' | 'REWORKER'

interface QualityEmployeeSelectProps {
  employees: QualityEmployee[]
  purpose?: EmployeePurpose
  value?: number | number[] | null
  onChange?: (value: number | number[] | undefined) => void
  onEmployeeSelected?: (employee?: QualityEmployee) => void
  multiple?: boolean
  allowClear?: boolean
  disabled?: boolean
  placeholder?: string
  id?: string
  'aria-describedby'?: string
  'aria-invalid'?: boolean
  'aria-required'?: boolean
}

function supportsPurpose(employee: QualityEmployee, purpose: EmployeePurpose) {
  return purpose === 'INSPECTOR'
    ? ['INSPECTOR', 'BOTH'].includes(employee.role)
    : ['REWORKER', 'BOTH'].includes(employee.role)
}

function purposeText(purpose: EmployeePurpose) {
  return purpose === 'INSPECTOR' ? '品检员' : '返工人员'
}

/**
 * Employee selector used inside quality workflows.
 *
 * Keeping quick creation beside the selector avoids a dead end when the
 * employee archive is empty.  Shipments still store stable employee IDs;
 * free-form names are first turned into a proper employee record.
 */
export function QualityEmployeeSelect({
  employees,
  purpose = 'INSPECTOR',
  value,
  onChange,
  onEmployeeSelected,
  multiple = false,
  allowClear = true,
  disabled = false,
  placeholder,
  id,
  'aria-describedby': ariaDescribedBy,
  'aria-invalid': ariaInvalid,
  'aria-required': ariaRequired,
}: QualityEmployeeSelectProps) {
  const queryClient = useContext(QueryClientContext)
  const { message } = App.useApp()
  const [newName, setNewName] = useState('')
  const [creating, setCreating] = useState(false)
  const creatingRef = useRef(false)
  const [createdEmployees, setCreatedEmployees] = useState<QualityEmployee[]>([])

  const allEmployees = useMemo(() => {
    const byId = new Map<number, QualityEmployee>()
    employees.forEach((employee) => byId.set(employee.id, employee))
    createdEmployees.forEach((employee) => byId.set(employee.id, employee))
    return [...byId.values()]
  }, [createdEmployees, employees])

  const selectedIds = new Set(
    (Array.isArray(value) ? value : value == null ? [] : [value]).map(Number),
  )
  const selectable = allEmployees.filter((employee) => employee.is_active && supportsPurpose(employee, purpose))
  const options = allEmployees
    .filter((employee) => selectable.some((item) => item.id === employee.id) || selectedIds.has(employee.id))
    .map((employee) => ({
      value: employee.id,
      label: `${employee.employee_no} · ${employee.name}${employee.team ? ` · ${employee.team}` : ''}${employee.is_active ? '' : ' · 已停用'}`,
      disabled: !employee.is_active || !supportsPurpose(employee, purpose),
    }))

  const selectEmployee = (employee: QualityEmployee) => {
    if (multiple) {
      const current = Array.isArray(value) ? value.map(Number) : []
      onChange?.([...new Set([...current, employee.id])])
    } else {
      onChange?.(employee.id)
    }
    onEmployeeSelected?.(employee)
  }

  const changeSelection = (next: number | number[] | undefined) => {
    onChange?.(next)
    if (!multiple) {
      const id = next == null || Array.isArray(next) ? undefined : Number(next)
      onEmployeeSelected?.(allEmployees.find((employee) => employee.id === id))
    }
  }

  const createAndSelect = async () => {
    const name = newName.trim()
    if (!name || creatingRef.current) return
    creatingRef.current = true
    setCreating(true)
    try {
      const employee = await qualityApi.quickResolveEmployee({
        name,
        purpose: purpose as Extract<QualityEmployeeRole, 'INSPECTOR' | 'REWORKER'>,
      })
      setCreatedEmployees((current) => [...current, employee])
      selectEmployee(employee)
      setNewName('')
      if (queryClient) {
        queryClient.setQueryData<QualityEmployee[]>(['quality', 'employees'], (current = []) => (
          current.some((item) => item.id === employee.id) ? current : [...current, employee]
        ))
        void Promise.all([
          queryClient.invalidateQueries({ queryKey: ['quality', 'employees'] }),
          queryClient.invalidateQueries({ queryKey: ['analytics'] }),
        ])
      }
      message.success(`${employee.name}已加入员工名单并选中`)
    } catch (error) {
      message.error((error as Error).message || `新增${purposeText(purpose)}失败`)
    } finally {
      creatingRef.current = false
      setCreating(false)
    }
  }

  return <div className="quality-employee-select">
    <Select
      mode={multiple ? 'multiple' : undefined}
      id={id}
      aria-describedby={ariaDescribedBy}
      aria-invalid={ariaInvalid}
      aria-required={ariaRequired}
      allowClear={allowClear}
      disabled={disabled}
      showSearch
      optionFilterProp="label"
      value={value == null ? undefined : value}
      onChange={(next) => changeSelection(next as number | number[] | undefined)}
      options={options}
      maxTagCount={multiple ? 'responsive' : undefined}
      placeholder={placeholder || (selectable.length ? `选择${purposeText(purpose)}` : `尚无${purposeText(purpose)}，请在下方新增`)}
      notFoundContent={<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={`暂无可选${purposeText(purpose)}`} />}
      style={{ width: '100%' }}
    />
    <div className="quality-employee-quick-create">
      <Input
        aria-label={`快速新增${purposeText(purpose)}姓名`}
        value={newName}
        disabled={disabled}
        maxLength={100}
        placeholder={`名单中没有？输入姓名新增${purposeText(purpose)}`}
        onChange={(event) => setNewName(event.target.value)}
        onPressEnter={() => void createAndSelect()}
      />
      <Button
        icon={<PlusOutlined />}
        loading={creating}
        disabled={disabled || !newName.trim()}
        onClick={() => void createAndSelect()}
      >新增并选中</Button>
    </div>
    {!selectable.length && <Typography.Text type="secondary" className="quality-employee-empty-hint">员工库为空也可直接继续录入；工号由系统自动生成，之后可在“员工档案”修改。</Typography.Text>}
  </div>
}
