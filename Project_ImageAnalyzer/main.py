import os
import threading
from concurrent.futures import ProcessPoolExecutor, as_completed
import cv2
import numpy as np
import pandas as pd
from PIL import Image
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import math

def open_tiff(path):
    img = np.array(Image.open(path))
    if img.ndim == 2:
        return img.astype(np.float32)
    return np.dot(img[..., :3], [0.2989, 0.5870, 0.1140]).astype(np.float32)

def split_tiles(img, k):
    h, w = img.shape
    tiles = []
    idx = 1
    for i in range(k):
        y0, y1 = (i * h) // k, ((i + 1) * h) // k if i < k - 1 else h
        for j in range(k):
            x0, x1 = (j * w) // k, ((j + 1) * w) // k if j < k - 1 else w
            tiles.append((idx, img[y0:y1, x0:x1].copy(), x0, y0))
            idx += 1
    return tiles

def detect_objects(tile_tuple, threshold_sigma=3.0, min_area=5):
    idx, tile, x0, y0 = tile_tuple
    if tile.size == 0:
        return []

    mu = float(np.mean(tile))
    sigma = float(np.std(tile))
    thresh = mu + threshold_sigma * sigma

    bw = (tile > thresh).astype(np.uint8) * 255
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    contours, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    objs = []

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < min_area:
            continue

        M = cv2.moments(cnt)
        if M.get('m00', 0) == 0:
            continue

        cx = M['m10'] / M['m00']
        cy = M['m01'] / M['m00']

        mask = np.zeros_like(tile, dtype=np.uint8)
        cv2.drawContours(mask, [cnt], -1, 255, -1)
        pixels = tile[mask > 0]
        if pixels.size == 0:
            continue

        brightness_sum = float(np.sum(pixels))
        mean_brightness = float(np.mean(pixels))
        max_pixel = float(np.max(pixels))

        perimeter = float(cv2.arcLength(cnt, True))
        compactness = (4 * np.pi * area) / (perimeter ** 2) if perimeter > 0 else 0.0

        eccentricity = 0.0
        if len(cnt) >= 5:
            (_, _), (MA, ma), _ = cv2.fitEllipse(cnt)
            major_axis = max(MA, ma)
            minor_axis = min(MA, ma)
            if major_axis > 0:
                eccentricity = float(np.sqrt(1 - (minor_axis / major_axis) ** 2))

        if area < 20 and brightness_sum > 1000:
            obj_type = 'star'
            color = (0, 255, 255)
        elif area < 50:
            obj_type = 'planet'
            color = (255, 0, 0)
        elif area < 300:
            obj_type = 'comet'
            color = (0, 255, 0)
        else:
            obj_type = 'galaxy'
            color = (0, 0, 255)

        objs.append({
            'tile_index': int(idx),
            'type': obj_type,
            'area': float(area),
            'brightness_sum': brightness_sum,
            'mean_brightness': mean_brightness,
            'max_pixel': max_pixel,
            'perimeter': perimeter,
            'compactness': float(compactness),
            'eccentricity': float(eccentricity),
            'centroid_y': float(y0 + cy),
            'centroid_x': float(x0 + cx),
            'color': color,
        })

    return objs


def create_visualization(img_path, objects, output_path):
    img = open_tiff(img_path)
    img_uint8 = np.clip(img, 0, 255).astype(np.uint8)
    out = cv2.cvtColor(img_uint8, cv2.COLOR_GRAY2BGR)

    for o in objects:
        x = int(round(o['centroid_x']))
        y = int(round(o['centroid_y']))
        size = int(max(2, math.sqrt(max(1.0, o['area'])))) + 2

        color = tuple(int(c) for c in o.get('color', (255, 255, 255)))

        cv2.rectangle(out,
                      (x - size, y - size),
                      (x + size, y + size),
                      color, 1)

    cv2.imwrite(output_path, out)

def process_all_images(folder, k, workers, outdir, progress_callback=None):
    tiff_files = [os.path.join(folder, f) for f in os.listdir(folder) 
                  if f.lower().endswith((".tif", ".tiff"))]
    
    all_results, image_objects = [], {}
    
    for file_idx, file_path in enumerate(tiff_files):
        if progress_callback:
            progress_callback(f"Обработка {file_idx + 1}/{len(tiff_files)}: {os.path.basename(file_path)}")
        
        img = open_tiff(file_path)
        tiles = split_tiles(img, k)
        file_objects = []
        
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(detect_objects, t) for t in tiles]
            for future in as_completed(futures):
                result = future.result()
                if result:
                    for obj in result:
                        obj["source_image"] = os.path.basename(file_path)
                        all_results.append(obj)
                        file_objects.append(obj)
        
        image_objects[file_path] = file_objects
    
    if not all_results:
        return None, None
    
    df = pd.DataFrame(all_results)
    
    summary = df.groupby("type").agg({
        "area": ["mean", "count", "std"],
        "brightness_sum": ["mean", "std"],
        "mean_brightness": ["mean", "std"],
        "eccentricity": ["mean", "std"],
        "compactness": ["mean", "std"]
    })
    
    os.makedirs(outdir, exist_ok=True)
    df.to_csv(os.path.join(outdir, "all_detected_objects.csv"), index=False)
    summary.to_csv(os.path.join(outdir, "summary_statistics.csv"))
    
    if progress_callback:
        progress_callback("Создание визуализаций...")
    
    vis_folder = os.path.join(outdir, "visualizations")
    os.makedirs(vis_folder, exist_ok=True)
    
    for file_path, objects in image_objects.items():
        if objects:
            base_name = os.path.splitext(os.path.basename(file_path))[0]
            vis_path = os.path.join(vis_folder, f"{base_name}_detected.jpg")
            create_visualization(file_path, objects, vis_path)
    return df, summary

class App:
    def __init__(self, root):
        self.root = root
        root.title("Analyzer")
        root.geometry("900x700")
        
        main_frame = ttk.Frame(root, padding=10)
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        
        self.images_folder = tk.StringVar()
        self.output_folder = tk.StringVar()
        self.k_var = tk.IntVar(value=5)
        self.workers_var = tk.IntVar(value=os.cpu_count() - 1 if os.cpu_count() > 1 else 1)
        self.status = tk.StringVar(value="Ожидание")
        
        fields = [
            ("Папка с TIFF:", self.images_folder, self.choose_input),
            ("Папка для результата:", self.output_folder, self.choose_output)
        ]
        
        for row, (label, var, cmd) in enumerate(fields):
            ttk.Label(main_frame, text=label).grid(row=row, column=0, sticky=tk.W, pady=5)
            ttk.Entry(main_frame, textvariable=var, width=50).grid(row=row, column=1, sticky=(tk.W, tk.E), pady=5)
            ttk.Button(main_frame, text="Выбрать", command=cmd).grid(row=row, column=2, padx=5, pady=5)
        
        row = 2
        ttk.Label(main_frame, text="Разрезов (k):").grid(row=row, column=0, sticky=tk.W, pady=5)
        ttk.Entry(main_frame, textvariable=self.k_var, width=10).grid(row=row, column=1, sticky=tk.W, pady=5)
        
        row += 1
        ttk.Label(main_frame, text="Потоки:").grid(row=row, column=0, sticky=tk.W, pady=5)
        ttk.Entry(main_frame, textvariable=self.workers_var, width=10).grid(row=row, column=1, sticky=tk.W, pady=5)
        
        row += 1
        ttk.Button(main_frame, text="Старт", command=self.start).grid(row=row, column=0, pady=10)
        
        row += 1
        ttk.Label(main_frame, textvariable=self.status).grid(row=row, column=0, columnspan=3, pady=5)
        
        row += 1
        self.result_label = ttk.Label(main_frame, text="")
        self.result_label.grid(row=row, column=0, columnspan=3, sticky=tk.W, pady=5)
        
        row += 1
        table_frame = ttk.Frame(main_frame)
        table_frame.grid(row=row, column=0, columnspan=3, sticky=(tk.W, tk.E, tk.N, tk.S), pady=10)
        main_frame.rowconfigure(row, weight=1)
        
        vsb = ttk.Scrollbar(table_frame, orient="vertical")
        hsb = ttk.Scrollbar(table_frame, orient="horizontal")
        
        self.column_headers = {
            "tile_index": "Номер фрагмента",
            "type": "Тип объекта",
            "area": "Площадь",
            "brightness_sum": "Суммарная яркость",
            "centroid_y": "Координата Y",
            "centroid_x": "Координата X",
            "mean_brightness": "Средняя яркость",
            "eccentricity": "Эксцентриситет",
            "compactness": "Компактность",
            "perimeter": "Периметр"
        }

        columns = ("tile_index", "type", "area", "brightness_sum", "centroid_y", "centroid_x",
                   "mean_brightness", "eccentricity", "compactness", "perimeter")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings",
                                  yscrollcommand=vsb.set, xscrollcommand=hsb.set, height=15)
        
        vsb.config(command=self.tree.yview)
        hsb.config(command=self.tree.xview)
        
        for col in columns:
            self.tree.heading(col, text=self.column_headers.get(col, col))
            self.tree.column(col, width=130, anchor=tk.CENTER)

        
        self.tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        vsb.grid(row=0, column=1, sticky=(tk.N, tk.S))
        hsb.grid(row=1, column=0, sticky=(tk.W, tk.E))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
    
    def choose_input(self):
        folder = filedialog.askdirectory()
        if folder:
            self.images_folder.set(folder)
    
    def choose_output(self):
        folder = filedialog.askdirectory()
        if folder:
            self.output_folder.set(folder)
    
    def update_status(self, message):
        self.status.set(message)
    
    def update_table(self, df):
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        for _, row in df.iterrows():
            values = tuple(f"{row.get(col, 0):.2f}" if isinstance(row.get(col, 0), float) 
                          else row.get(col, "") for col in self.tree["columns"])
            self.tree.insert("", tk.END, values=values)
    
    def start(self):
        if not self.images_folder.get():
            messagebox.showerror("Ошибка", "Выберите папку с TIFF изображениями")
            return
        if not self.output_folder.get():
            messagebox.showerror("Ошибка", "Выберите папку для результатов")
            return
        threading.Thread(target=self.run, daemon=True).start()
    
    def run(self):
        try:
            self.update_status("Обработка...")
            df, summary = process_all_images(self.images_folder.get(), self.k_var.get(),
                                            self.workers_var.get(), self.output_folder.get(),
                                            progress_callback=self.update_status)
            
            if df is None:
                self.update_status("Объекты не найдены")
                messagebox.showinfo("Результат", "Объекты не найдены")
            else:
                csv_path = os.path.join(self.output_folder.get(), "all_detected_objects.csv")
                self.result_label.config(text=f"Готово. Найдено объектов: {len(df)}. CSV: {csv_path}")
                self.update_status(f"Готово. Найдено объектов: {len(df)}")
                self.update_table(df)
                messagebox.showinfo("Готово",
                    f"Анализ завершен!\n\nНайдено объектов: {len(df)}\n"
                    f"Результаты: {self.output_folder.get()}\n"
                    f"Визуализации: {os.path.join(self.output_folder.get(), 'visualizations')}")
        except Exception as e:
            self.update_status(f"Ошибка: {str(e)}")
            messagebox.showerror("Ошибка", f"Произошла ошибка:\n{str(e)}")

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()