how to run this program

## Persyaratan Sistem

- Python 3.8 atau lebih baru
- Sistem Operasi:
  - Windows
  - Linux
  - macOS

---

## Instalasi

### 1. Clone Repository

```bash
git clone <repository-url>
```

atau download file ZIP dan ekstrak.

### 2. Masuk ke Folder Project

```bash
cd SUDOKU-DLX-SEQUENTIAL-AND-PARALLEL
```

### 3. Cek Versi Python

```bash
python --version
```

atau

```bash
python3 --version
```

Pastikan Python telah terinstal dengan benar.

---

# Menjalankan Program

## A. Menjalankan DLX Sequential

Buka terminal pada folder project lalu jalankan:

```bash
python sudoku_normal.py
```

atau

```bash
python3 sudoku_normal.py
```

Program akan:

1. Meminta input parameter.
2. Mengubah puzzle ke Exact Cover Matrix.
3. Menjalankan algoritma Dancing Links (DLX).
4. Menampilkan solusi Sudoku.
5. Menampilkan waktu eksekusi.

---

## B. Menjalankan DLX Parallel

Buka terminal pada folder project lalu jalankan:

```bash
python sudoku_parallel.py
```

atau

```bash
python3 sudoku_parallel.py
```

Program akan:

1. Meminta input parameter.
2. Mengubah puzzle ke Exact Cover Matrix.
3. Menjalankan algoritma Dancing Links (DLX).
4. Menampilkan solusi Sudoku.
5. Menampilkan waktu eksekusi.